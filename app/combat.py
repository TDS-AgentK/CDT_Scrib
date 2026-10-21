"""Combat des fiches de jeu (/attaque) : le site arbitre, le bot ne fait que l'interface Discord.

Même principe que Rostheim et les jets du site : le bot et le site ne s'appellent pas, ils passent par la base.
- /attaque : le bot dépose un combat au statut « demande » (demande = proposer, avec l'identifiant Discord du joueur) ;
- Accepter / Refuser / votes d'égalité : le bot dépose la demande sur le combat (champ `demande`) ;
dans les deux cas avec `demande_statut = "a_traiter"`. Le site traite la file (jets, états, dégâts) puis note
« traitee » ou « erreur » (`demande_erreur`). Le bot « réveille » aussi le site par une simple adresse publique
(/api/combat-file, sans mot de passe) pour qu'il traite la demande tout de suite.
Le bot lit ensuite la collection `combats` pour publier :
- les combats lancés depuis le site (bouton « Attaquer » de la fiche) ;
- chaque changement de statut (en attente → refusé / expiré / égalité / résolu), en modifiant le même message.
`combats.discord_poste` garde le dernier statut affiché : tout combat dont le statut a changé depuis est republié.
Boutons : custom_id « eco:cbt:<id>:ok|no » (accepter / refuser), « eco:cbt:<id>:va|vd » (avantage attaquant / défenseur),
« eco:cbt:<id>:ta|ec » (Try again / Échec critique après les jets) ; menu « eco:cbt:<id>:obj » (accepter avec un objet).
Objets de hasard (inventaire global du joueur, consommés à l'usage) : l'attaquant en choisit un dans /attaque, le
défenseur dans le menu en acceptant ; après les jets, Try again (10 min) et Échec critique (30 s) recalculent l'issue.
Les listes de /attaque (vos fiches, leurs armes et sorts, les cibles) sont lues directement dans la base.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import discord
import httpx

from app.pocketbase import PocketBase, echapper

log = logging.getLogger(__name__)

VERIFIER_S = 5
FRAICHEUR = timedelta(minutes=30)  # un combat plus ancien n'est plus publié (bot arrêté longtemps)
OR_CDT, VERT, ROUGE, GRIS = 0xC9A24C, 0x6FAE7A, 0xB0473B, 0x7D7F86
NOMS_MODE = {"avantage": "avantage", "desavantage": "désavantage", "maximum": "maximum"}
NOMS_SORT = {"mineur": "mineur", "median": "médian", "majeur": "majeur"}
# Adresse publique du site, seulement pour le « réveiller » (facultatif : sans réponse, il traite la file à la visite suivante).
SITE_PAR_DEFAUT = "https://cdtsite-production.up.railway.app"

# Objets de hasard : clé du site → nom affiché et noms du catalogue (eco_objets). Même table que src/lib/hasard.ts.
OBJETS = {
    "trefle": ("🍀 Trèfle à 10 feuilles", ["Trèfle à 10 feuilles"]),
    "reussite_critique": ("⭐ Réussite critique", ["Réussite critique en Donjon"]),
    "compte_pour_un": ("⚖️ Ça ne compte que pour un", ["Ça ne compte quand même que pour un", "Ca ne compte quand même que pour un"]),
    "try_again": ("🔁 Try again", ["Try again"]),
    "echec_critique": ("💀 Échec critique", ["Echec critique en donjon"]),
    "brise_garde": ("🛡️💥 Brise-garde", ["Brise Garde 🛡️"]),
    "verre_plein": ("🍷 Verre à moitié plein", ["Verre à moitié plein"]),
    "verre_vide": ("🥛 Verre à moitié vide", ["Verre à moitié vide"]),
}
POUR_ATTAQUANT = ["brise_garde", "verre_plein", "trefle", "reussite_critique", "compte_pour_un"]
POUR_DEFENSEUR = ["verre_vide", "trefle", "reussite_critique", "compte_pour_un"]
DELAI_ECHEC = timedelta(seconds=30)
DELAI_TRY_AGAIN = timedelta(minutes=10)


async def objets_hasard(pb: PocketBase, joueur_id: str) -> dict:
    """Quantité de chaque objet de hasard d'un joueur (inventaire global)."""
    par_nom = {n: k for k, (_, noms) in OBJETS.items() for n in noms}
    out: dict[str, int] = {}
    for ligne in await pb.lister("eco_inventaire", f'joueur="{echapper(joueur_id)}" && quantite>0', expand="objet"):
        cle = par_nom.get(((ligne.get("expand") or {}).get("objet") or {}).get("nom") or "")
        if cle:
            out[cle] = out.get(cle, 0) + (ligne.get("quantite") or 0)
    return out


# ---------------------------------------------------------------- demandes déposées dans la base

async def _reveiller():
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.get((os.getenv("SITE_URL") or SITE_PAR_DEFAUT).rstrip("/") + "/api/combat-file")
    except httpx.HTTPError:
        pass  # le site traitera la file à sa prochaine requête


async def _attendre(pb: PocketBase, combat_id: str, secondes: float = 12) -> dict:
    """Attend que le site ait traité la demande ; lève RuntimeError avec son message en cas de refus."""
    for _ in range(int(secondes / 0.5)):
        c = await pb.requete("GET", f"/api/collections/combats/records/{combat_id}")
        if c.get("demande_statut") == "erreur":
            raise RuntimeError(c.get("demande_erreur") or "Demande refusée par le site.")
        if c.get("demande_statut") == "traitee":
            return c
        await asyncio.sleep(0.5)
    raise RuntimeError("Le site n'a pas encore traité la demande : réessayez dans un instant.")


async def _attendre_jet(pb: PocketBase, jet_id: str, secondes: float = 12) -> dict:
    """Comme _attendre, pour une demande déposée sur un jet (objets de hasard sous un jet du site)."""
    for _ in range(int(secondes / 0.5)):
        j = await pb.requete("GET", f"/api/collections/jets/records/{jet_id}", params={"expand": "personnage"})
        if j.get("demande_statut") == "erreur":
            raise RuntimeError(j.get("demande_erreur") or "Demande refusée par le site.")
        if j.get("demande_statut") == "traitee":
            return j
        await asyncio.sleep(0.5)
    raise RuntimeError("Le site n'a pas encore traité la demande : réessayez dans un instant.")


async def proposer(pb: PocketBase, discord_id: int, fiche: str, attaque: str, cible: str, salon_id: str, objet: str | None = None) -> dict:
    """/attaque : dépose la demande, attend le site, renvoie le combat ; en cas de refus, la demande est retirée."""
    if not fiche or fiche == "-" or not attaque or attaque == "-" or not cible or cible == "-":
        raise RuntimeError("Choisissez votre personnage, une arme ou un sort, et une cible dans les listes.")
    d = await pb.creer("combats", {
        "statut": "demande", "origine": "discord", "salon_id": str(salon_id or ""), "discord_poste": "demande",
        "demande": {"action": "proposer", "discord_id": str(discord_id), "fiche": fiche, "attaque": attaque, "defenseur": cible,
                    "salon_id": str(salon_id or ""), "objet": objet if objet and objet != "-" else ""},
        "demande_statut": "a_traiter",
    })
    await _reveiller()
    try:
        return await _attendre(pb, d["id"])
    except RuntimeError:
        await pb.supprimer("combats", d["id"])
        raise


async def agir(pb: PocketBase, discord_id: int, combat_id: str, choix: str) -> dict:
    """Bouton d'un combat : ok / no (répondre au défi), obj:<objet> (accepter avec un objet), va / vd (vote d'égalité),
    ta / ec (Try again / Échec critique après les jets)."""
    try:
        c = await pb.requete("GET", f"/api/collections/combats/records/{combat_id}")
    except RuntimeError as e:
        # Combat supprimé (essai effacé, nettoyage) : message clair plutôt que l'erreur brute de la base.
        if "404" in str(e):
            raise RuntimeError("Ce combat n'existe plus (combat d'essai ou supprimé) : lancez-en un nouveau avec /attaque.") from None
        raise
    if c.get("demande_statut") == "a_traiter":
        raise RuntimeError("Une action est déjà en cours sur ce combat : réessayez dans un instant.")
    demande = {"discord_id": str(discord_id)}
    if choix in ("ok", "no"):
        demande.update({"action": "repondre", "accepte": choix == "ok"})
    elif choix.startswith("obj:"):
        demande.update({"action": "repondre", "accepte": True, "objet": choix[4:]})
    elif choix in ("ta", "ec"):
        demande.update({"action": "apres", "quoi": "try_again" if choix == "ta" else "echec_critique"})
    else:
        demande.update({"action": "voter", "choix": "attaquant" if choix == "va" else "defenseur"})
    await pb.maj("combats", combat_id, {"demande": demande, "demande_statut": "a_traiter", "demande_erreur": ""})
    await _reveiller()
    return await _attendre(pb, combat_id)


# ---------------------------------------------------------------- listes de /attaque (lues dans la base)

def _nom_fiche(f: dict) -> str:
    return ((f.get("expand") or {}).get("personnage") or {}).get("prenom") or f.get("nom") or "?"


async def autocompletion(eco, membre, focus: str, options: dict, tape: str) -> list[dict]:
    """Choix proposés pour /attaque : perso (vos fiches), type (armes et sorts de la fiche choisie), cible, objet."""
    pb = eco.pb
    try:
        joueur = await eco.joueur_de(membre)
        if not joueur:
            return [{"name": "⚠ Votre compte Discord n'est relié à aucun joueur du site", "value": "-"}]
        pseudo = echapper(joueur.get("pseudo") or "")
        if focus == "perso":
            fiches = await pb.lister("fiches_jeu", f'genre!="mj" && personnage.joueur="{pseudo}"', expand="personnage")
            choix = [{"name": _nom_fiche(f)[:100], "value": f["id"]} for f in fiches]
            if not choix:
                return [{"name": "Aucune fiche de jeu : créez-la sur le site (dé gris de la page du personnage)", "value": "-"}]
        elif focus == "type":
            if not options.get("perso") or options.get("perso") == "-":
                return [{"name": "Choisissez d'abord votre personnage", "value": "-"}]
            f = await pb.requete("GET", f'/api/collections/fiches_jeu/records/{options["perso"]}')
            # Transformé : les armes et la magie de la forme active.
            src = next((x for x in f.get("formes") or [] if x.get("id") == f.get("forme_active") and x.get("mode") == "transformation"), f)
            choix = [{"name": a.get("nom", "?")[:100], "value": f"arme:{i + 1}"} for i, a in enumerate(src.get("armes") or [])
                     if a and a.get("nom") and a.get("nom") != "Pas d'armes"]
            if (src.get("magie") or {}).get("niveau"):
                courant = src["magie"].get("courant") or "magie"
                choix += [{"name": f"Sort {NOMS_SORT[t]} ({courant})"[:100], "value": f"sort:{t}"} for t in ("mineur", "median", "majeur")]
            if not choix:
                return [{"name": "Cette fiche n'a ni arme ni magie", "value": "-"}]
        elif focus == "cible":
            sessions = await pb.lister("sessions_jeu", 'statut="ouverte"')
            table = set((sessions[0].get("table") or []) if sessions else [])
            choix = []
            # Vos autres personnages sont proposés aussi (combat entre ses propres fiches) ; seule la fiche qui attaque est exclue.
            for f in await pb.lister("fiches_jeu", 'genre!="mj"', expand="personnage"):
                marque = "⚔ " if f["id"] in table else ""
                if f["id"] != options.get("perso"):
                    choix.append((f["id"] not in table, {"name": f"{marque}{_nom_fiche(f)}"[:100], "value": f'{f["id"]}:'}))
                formes = {x.get("id"): x for x in f.get("formes") or []}
                for i in f.get("invocations") or []:
                    fo = formes.get(i.get("forme_id"))
                    if fo:
                        lien = "compagnon" if fo.get("mode") == "compagnon" else "invocation"
                        choix.append((f["id"] not in table, {"name": f"{marque}{fo.get('nom')} ({lien} de {_nom_fiche(f)})"[:100], "value": f'{f["id"]}:{i["id"]}'}))
            choix = [c for _, c in sorted(choix, key=lambda x: (x[0], x[1]["name"]))]
        elif focus == "objet":
            stock = await objets_hasard(pb, joueur["id"])
            choix = [{"name": f"{OBJETS[k][0]} ×{stock[k]}"[:100], "value": k} for k in POUR_ATTAQUANT if stock.get(k)]
            if not choix:
                return [{"name": "Aucun objet de hasard utilisable à l'attaque dans votre inventaire", "value": "-"}]
        else:
            return []
    except Exception as e:
        log.exception("Autocomplétion /attaque")
        return [{"name": f"⚠ Erreur : {type(e).__name__}"[:100], "value": "-"}]
    return [c for c in choix if tape.lower() in c["name"].lower()][:25]


# ---------------------------------------------------------------- affichage

def _jet(j: dict | None) -> str:
    if not j:
        return "—"
    des = ", ".join(str(d) for d in j.get("des") or [])
    bonus = j.get("bonus") or 0
    txt = f"**{j.get('total')}** `{j.get('formule')}` [{des}]" + (f" {'+' if bonus > 0 else '−'} {abs(bonus)}" if bonus else "")
    mode = j.get("mode_jet") or ""
    if mode in ("avantage", "desavantage"):
        txt += f" · {NOMS_MODE[mode]} (écarté : {', '.join(str(d) for d in j.get('des_ecartes') or [])})"
    if j.get("naturel_max"):
        txt += " · ⭐ maximum naturel"
    elif j.get("naturel_min"):
        txt += " · 💀 face minimale"
    if j.get("objet_utilise"):
        txt += f" · {j['objet_utilise']}"
    return txt


def _ecoule(c: dict) -> timedelta:
    fin = _date(c.get("resolu_le") or c.get("updated"))
    return datetime.now(timezone.utc) - fin if fin else timedelta(days=1)


async def _mention(pb: PocketBase, user_id: str) -> str:
    """Mention Discord du joueur d'un compte du site (users → joueurs.discord_id)."""
    if not user_id:
        return ""
    try:
        u = await pb.requete("GET", f"/api/collections/users/records/{user_id}", params={"expand": "joueur"})
        did = ((u.get("expand") or {}).get("joueur") or {}).get("discord_id")
        return f"<@{did}>" if did else u.get("name") or ""
    except Exception:
        return ""


def _boutons(c: dict) -> list:
    if c["statut"] == "en_attente":
        return [{"type": 1, "components": [
            {"type": 2, "style": 3, "label": "Accepter", "custom_id": f'eco:cbt:{c["id"]}:ok'},
            {"type": 2, "style": 4, "label": "Refuser", "custom_id": f'eco:cbt:{c["id"]}:no'}]},
            # Le menu accepte directement avec l'objet choisi (le site vérifie l'inventaire du défenseur).
            {"type": 1, "components": [{"type": 3, "custom_id": f'eco:cbt:{c["id"]}:obj', "placeholder": "Accepter avec un objet de hasard…",
                                        "options": [{"label": OBJETS[k][0], "value": k} for k in POUR_DEFENSEUR]}]}]
    rangees = []
    if c["statut"] == "egalite":
        rangees.append({"type": 1, "components": [
            {"type": 2, "style": 1, "label": "Avantage attaquant", "custom_id": f'eco:cbt:{c["id"]}:va'},
            {"type": 2, "style": 2, "label": "Avantage défenseur", "custom_id": f'eco:cbt:{c["id"]}:vd'}]})
    if c["statut"] in ("resolu", "egalite"):
        # Après les jets : Try again (10 min) et Échec critique (30 s) ; la boucle retire les boutons une fois passés.
        apres = []
        if _ecoule(c) < DELAI_TRY_AGAIN:
            apres.append({"type": 2, "style": 2, "label": OBJETS["try_again"][0], "custom_id": f'eco:cbt:{c["id"]}:ta'})
        if _ecoule(c) < DELAI_ECHEC and c.get("attaquant_user") != c.get("defenseur_user"):
            apres.append({"type": 2, "style": 4, "label": OBJETS["echec_critique"][0], "custom_id": f'eco:cbt:{c["id"]}:ec'})
        if apres:
            rangees.append({"type": 1, "components": apres})
    return rangees


def signature_boutons(c: dict) -> str:
    """Boutons attendus : quand elle change (délai écoulé), la boucle met le message à jour."""
    return "|".join(b.get("custom_id", "") for r in _boutons(c) for b in r["components"])


async def message(pb: PocketBase, c: dict) -> dict:
    """Message Discord d'un combat (embed + boutons selon le statut). `c` : enregistrement combats avec ses jets."""
    ex = c.get("expand") or {}
    att, dfn = c.get("attaquant_nom") or "?", c.get("defenseur_nom") or "?"
    defenseur = await _mention(pb, c.get("defenseur_user"))
    lignes = [f"Avec : {c.get('action_libelle') or '?'}"]
    objets = [f"{OBJETS[c[k]][0]} ({qui})" for k, qui in (("objet_attaquant", "attaquant"), ("objet_defenseur", "défenseur")) if c.get(k) in OBJETS]
    if objets:
        lignes.append("Objets : " + ", ".join(objets))
    statut, couleur, contenu = c["statut"], OR_CDT, ""
    if statut == "en_attente":
        lignes.append(f"{defenseur or dfn}, acceptez-vous le combat ? (10 minutes ; le menu accepte avec un objet de hasard)")
        contenu = defenseur
    elif statut in ("refuse", "expire"):
        lignes.append("Défi refusé : rien n'est lancé." if statut == "refuse" else "Défi expiré : personne n'a répondu dans les 10 minutes.")
        couleur = GRIS
    else:
        lignes.append(f"⚔️ Attaque : {_jet(ex.get('jet_attaque'))}")
        lignes.append(f"🛡️ Défense : {_jet(ex.get('jet_defense')) if ex.get('jet_defense') else '— (Brise-garde : garde ignorée)'}")
        if statut == "egalite":
            votes = c.get("votes") or {}
            lignes.append("**Égalité** : chacun choisit ; en cas de désaccord, 1d2 tranche."
                          + (f" Votes : attaquant {'✓' if votes.get('attaquant') else '…'}, défenseur {'✓' if votes.get('defenseur') else '…'}" if votes else ""))
        elif c.get("issue") == "passe":
            lignes.append(f"**L'attaque surpasse la défense. {c.get('degats') or 0} points de dégâts.** {_jet(ex.get('jet_degats'))}")
            couleur = VERT
        else:
            lignes.append("**L'attaque ne surpasse pas la défense.**")
            couleur = ROUGE
        if c.get("detail") and statut in ("resolu", "egalite"):
            lignes.append(f"*{c['detail']}*")
    embed = {"title": f"⚔️ {att} attaque {dfn}", "description": "\n".join(lignes), "color": couleur,
             "footer": {"text": "Combat lancé depuis le site" if c.get("origine") == "site" else "Combat · fiches de jeu CDT"}}
    return {"content": contenu, "embeds": [embed], "components": _boutons(c), "allowed_mentions": {"parse": ["users"]}}


async def combat_complet(pb: PocketBase, combat_id: str) -> dict:
    return await pb.requete("GET", f"/api/collections/combats/records/{combat_id}", params={"expand": "jet_attaque,jet_defense,jet_degats"})


# ---------------------------------------------------------------- boucle : combats lancés depuis le site, expirations

def _date(brut) -> datetime | None:
    try:
        return datetime.fromisoformat(str(brut).replace("Z", "+00:00").replace(" ", "T")) if brut else None
    except ValueError:
        return None


async def publier(client: discord.Client, pb: PocketBase, c: dict):
    """Publie un combat (premier message) ou met à jour son message, puis note le statut affiché."""
    from app.eco_vues import vue_discord
    corps = await message(pb, c)
    salon = client.get_channel(int(c["salon_id"])) or await client.fetch_channel(int(c["salon_id"]))
    embed, vue = discord.Embed.from_dict(corps["embeds"][0]), vue_discord(corps["components"])
    maj = {"discord_poste": c["statut"]}
    if c.get("discord_message_id"):
        await salon.get_partial_message(int(c["discord_message_id"])).edit(content=corps["content"] or None, embed=embed, view=vue)
    else:
        m = await salon.send(content=corps["content"] or None, embed=embed, view=vue,
                             allowed_mentions=discord.AllowedMentions(users=True, everyone=False, roles=False))
        maj["discord_message_id"] = str(m.id)
    await pb.maj("combats", c["id"], maj)


_attente_discord: dict[str, datetime] = {}  # combats /attaque pas encore publiés par la commande : vus pour la première fois à
_signatures: dict[str, str] = {}  # boutons affichés des combats récemment joués (Try again / Échec critique)


async def boucle(client: discord.Client, pb: PocketBase):
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            maintenant = datetime.now(timezone.utc)
            # Défis non acceptés à temps.
            for c in await pb.lister("combats", 'statut="en_attente"'):
                fin = _date(c.get("expire_le"))
                if fin and fin < maintenant:
                    await pb.maj("combats", c["id"], {"statut": "expire", "detail": "Défi non accepté dans les 10 minutes."})
            # Combats dont le statut a changé depuis le dernier affichage (dont ceux lancés depuis le site).
            # Tri en Python : la collection peut ne pas avoir de champ « created » (PocketBase ≥ 0.23).
            a_publier = await pb.lister("combats", 'salon_id!="" && statut!=discord_poste')
            for c in sorted(a_publier, key=lambda x: str(x.get("created") or "")):
                if c.get("origine") == "discord" and not c.get("discord_message_id"):
                    # /attaque publie lui-même son défi juste après le traitement du site : on lui laisse 30 s,
                    # sinon la boucle passerait parfois avant lui et le défi serait posté deux fois.
                    vu = _attente_discord.setdefault(c["id"], maintenant)
                    if maintenant - vu < timedelta(seconds=30):
                        continue
                _attente_discord.pop(c["id"], None)
                quand = _date(c.get("created"))
                if quand and maintenant - quand > FRAICHEUR and not c.get("discord_message_id"):
                    await pb.maj("combats", c["id"], {"discord_poste": c["statut"]})
                    continue
                try:
                    await publier(client, pb, await combat_complet(pb, c["id"]))
                except Exception:
                    log.exception("Combat %s non publié (salon %s)", c.get("id"), c.get("salon_id"))
                    await pb.maj("combats", c["id"], {"discord_poste": c["statut"]})
            # Délais des objets de hasard écoulés : on retire les boutons Try again / Échec critique du message.
            depuis = (maintenant - DELAI_TRY_AGAIN - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
            for c in await pb.lister("combats", f'discord_message_id!="" && resolu_le>="{depuis}" && statut=discord_poste'):
                sig = signature_boutons(c)
                if c["id"] in _signatures and _signatures[c["id"]] != sig:
                    try:
                        await publier(client, pb, await combat_complet(pb, c["id"]))
                    except Exception:
                        log.exception("Combat %s : boutons non retirés", c.get("id"))
                _signatures[c["id"]] = sig
        except Exception:
            log.exception("Erreur dans la boucle des combats")
        await asyncio.sleep(VERIFIER_S)
