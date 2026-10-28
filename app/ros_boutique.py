"""Boutiques de Rostheim dans /boutique : une boutique par domaine (récompenses ros_recompenses payées dans la monnaie du
domaine). Les effets simples s'appliquent tout seuls (Or, XP, rôle, objet, multiplicateur temporaire) ; les récompenses
« spéciales » (coche sur le site) et celles que le bot ne sait pas appliquer deviennent une demande (ros_demandes) postée
dans le salon du staff, après une fenêtre où le joueur précise ce qu'il veut. Chaque achat est tracé dans eco_gains.
Une récompense « pleine lune » (champ disponibilite) n'est visible et achetable que pendant la pleine lune de Davos ou
d'Amarante (calcul du site, app/lunes.py)."""
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import discord

from app import eco_vues, lunes
from app.eco_cartes import abrege
from app.economie import COULEUR_DEFAUT, meme_nom, normaliser
from app.pocketbase import echapper

log = logging.getLogger("cdt_scrib.ros_boutique")
SALON_DEMANDES = int(os.environ.get("ROS_SALON_DEMANDES", "1504405642084093982"))
PREFIXE = "r_"  # identifiant de boutique Rostheim : « r_<id du domaine> » (jamais un id PocketBase)
SALONS_MOTS = {"quiz": ["quiz"], "texte libre": ["textelibre", "texteslibres"]}


def _pb_date(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")


def _facteur(txt: str) -> float:
    return float(txt.replace(",", "."))


def analyser(r: dict) -> dict:
    """Effet automatique d'une récompense, d'après son libellé ; {"type": "demande"} si le bot ne sait pas l'appliquer."""
    lib = (r.get("libelle") or "").strip()
    if r.get("speciale"):
        return {"type": "demande"}
    if r.get("objet"):
        return {"type": "objet"}
    if m := re.match(r"gagner (\d+) (gold|or)\b", lib, re.I):
        return {"type": "or", "n": int(m[1])}
    if m := re.match(r"gagner (\d+) xp\b", lib, re.I):
        return {"type": "xp", "n": int(m[1])}
    if m := re.match(r"obtenir le r[ôo]le (.+)", lib, re.I):
        return {"type": "role", "nom": m[1].strip()}
    if m := re.match(r"trois jours de x(\d+(?:[.,]\d+)?) (gold|or|xp) sur le salon (.+)", lib, re.I):
        return {"type": "mult", "facteur": _facteur(m[1]), "monnaie": "xp" if m[2].lower() == "xp" else "or",
                "salon": m[3].strip().lower(), "jours": 3}
    if m := re.match(r"activer le x(\d+(?:[.,]\d+)?) pour les messages de plus de (\d+) caract[èe]res pour (?:le |l')?(gold|or|xp)\b.*?(\d+)\s*h", lib, re.I):
        return {"type": "mult", "facteur": _facteur(m[1]), "monnaie": "xp" if m[3].lower() == "xp" else "or",
                "min_car": int(m[2]), "jours": int(m[4]) / 24}
    return {"type": "demande"}


# ---------------------------------------------------------------- lecture

async def domaines(eco) -> list[dict]:
    """Domaines dont la boutique est ouverte (événement Rostheim actif)."""
    if not (await eco.config())["reglages"].get("rostheim_actif"):
        return []
    return await eco.pb.lister("ros_domaines", tri="ordre")


async def articles(eco, domaine: dict) -> list[dict]:
    """Récompenses en vente maintenant dans ce domaine (celles d'une pleine lune absente sont masquées)."""
    liste = await eco.pb.lister("ros_recompenses", f'actif=true && (domaine="{echapper(domaine["id"])}" || toute_monnaie=true)', tri="ordre")
    return [r for r in liste if lunes.disponible(r)]


async def solde(eco, joueur: dict | None, domaine: dict) -> dict | None:
    if not joueur:
        return None
    return await eco.pb.premier("ros_soldes", f'joueur="{echapper(joueur["id"])}" && domaine="{echapper(domaine["id"])}"')


def option(d: dict) -> dict:
    return {"label": f'{d.get("nom")} — {d.get("monnaie_nom")}'[:100], "value": PREFIXE + d["id"], "description": "Boutique de Rostheim"}


# ---------------------------------------------------------------- vue

async def vue(eco, membre, boutique_id: str, page: int = 0) -> dict:
    cfg = await eco.config()
    tous = await domaines(eco)
    d = next((x for x in tous if PREFIXE + x["id"] == boutique_id), None)
    if not d:
        return eco_vues.erreur_v2("Cette boutique n'est pas ouverte.")
    liste = await articles(eco, d)
    pages = max(1, -(-len(liste) // eco_vues.PAR_PAGE_BOUTIQUE))
    page = max(0, min(page, pages - 1))
    joueur = await eco.joueur_de(membre)
    monnaie = d.get("monnaie_nom") or "monnaie"
    s = await solde(eco, joueur, d)
    blocs = [eco_vues._texte(f'## {d.get("nom")} · {monnaie}\nBoutique de Rostheim.'), {"type": 14, "divider": True, "spacing": 1}]
    for a in liste[page * eco_vues.PAR_PAGE_BOUTIQUE:(page + 1) * eco_vues.PAR_PAGE_BOUTIQUE]:
        lignes = [f'### {a["libelle"]}']
        notes = [lunes.mention(a)] if lunes.mention(a) else []
        if a.get("speciale"):
            notes.append("✨ Récompense spéciale : le staff la réalise après votre demande")
        notes.append("Communautaire" if a.get("portee") == "communautaire" else "Individuelle")
        lignes.append("-# " + " · ".join(notes))
        blocs.append({"type": 9, "components": [eco_vues._texte("\n".join(lignes)[:1500])],
                      "accessory": eco_vues._bouton(f'{eco_vues._n(a.get("prix"))} {monnaie} - Acheter'[:80], f'eco:rbuy:{d["id"]}:{a["id"]}', style=1)})
    if not liste:
        blocs.append(eco_vues._texte("*Aucune récompense pour le moment.*"))
    blocs.append({"type": 14, "divider": True, "spacing": 1})
    blocs.append(eco_vues._texte(f'-# Vous avez {abrege((s or {}).get("monnaie") or 0)} {monnaie}'))
    blocs.append({"type": 1, "components": [
        eco_vues._bouton("Précédent", f"eco:pg:{boutique_id}:{page - 1}:ordre", disabled=page == 0),
        eco_vues._bouton(f"Page {page + 1}/{pages}", "eco:rien", disabled=True),
        eco_vues._bouton("Suivant", f"eco:pg:{boutique_id}:{page + 1}:ordre", style=1, disabled=page >= pages - 1),
        eco_vues._bouton("Fermer", "eco:fermer"),
    ]})
    composants = [{"type": 17, "accent_color": eco_vues.OR_DEFAUT, "components": blocs}]
    autres = await eco_vues.options_boutiques(eco, cfg, membre, exclu=boutique_id)
    if autres:
        composants.append({"type": 1, "components": [{"type": 3, "custom_id": "eco:shop", "placeholder": "Sélectionnez une autre boutique…", "options": autres}]})
    return {"flags": eco_vues.V2 | eco_vues.PRIVE, "components": composants}


async def fenetre(eco, domaine_id: str, rec_id: str) -> dict | None:
    """Fenêtre posée avant l'achat d'une récompense spéciale : le joueur dit ce qu'il veut précisément."""
    r = await eco.pb.premier("ros_recompenses", f'id="{echapper(rec_id)}"')
    if not r or not analyser(r)["type"] == "demande":
        return None
    return {"title": "Votre demande", "custom_id": f"eco:rmod:{domaine_id}:{rec_id}", "components": [
        {"type": 1, "components": [{"type": 4, "custom_id": "precision", "style": 2, "label": "Que souhaitez-vous précisément ?",
                                     "placeholder": (r.get("libelle") or "")[:100], "min_length": 3, "max_length": 1000, "required": True}]},
    ]}


# ---------------------------------------------------------------- achat

async def acheter(eco, membre: discord.Member, domaine_id: str, rec_id: str, precision: str = "", channel_id: str | None = None) -> tuple[bool, str]:
    cfg = await eco.config()
    d = next((x for x in await domaines(eco) if x["id"] == domaine_id), None)
    if not d:
        return False, "Cette boutique n'est pas ouverte."
    r = await eco.pb.premier("ros_recompenses", f'id="{echapper(rec_id)}" && actif=true')
    if not r or not (r.get("domaine") == d["id"] or r.get("toute_monnaie")):
        return False, "Cette récompense n'est plus disponible."
    if not lunes.disponible(r):
        return False, f"Cette récompense ne s'achète que pendant la p{lunes.pleine_lune_de(lunes.DISPONIBILITES[r['disponibilite']])[1:]}."
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return False, "Aucune fiche joueur liée à ce compte Discord."
    monnaie, prix = d.get("monnaie_nom") or "monnaie", r.get("prix") or 0
    effet = analyser(r)
    salons: list[str] = []
    if effet["type"] == "mult" and effet.get("salon"):
        mots = SALONS_MOTS.get(effet["salon"], [normaliser(effet["salon"])])
        salons = [s["id"] for s in cfg["salons"] if any(m in normaliser(s.get("salon_nom")) for m in mots)]
        if not salons:
            effet = {"type": "demande"}  # salon introuvable : le staff s'en occupe
    async with eco._verrou(membre.id):
        sd = await solde(eco, joueur, d)
        if not sd or (sd.get("monnaie") or 0) < prix:
            return False, f'Il faut {prix} {monnaie} (vous en avez {(sd or {}).get("monnaie") or 0}).'
        if effet["type"] == "role":
            if eco._a_le_role(membre, None, effet["nom"]):
                return False, f'Vous avez déjà le rôle « {effet["nom"]} » : rien n\'a été prélevé.'
            echec = await eco.donner_role(membre, joueur, None, effet["nom"], 0)
            if echec:
                return False, f"Achat annulé, rien n'a été prélevé : {echec}."
        await eco.pb.maj("ros_soldes", sd["id"], {"monnaie": sd["monnaie"] - prix})
        maintenant = _pb_date(datetime.now(timezone.utc))
        nom = f'boutique {d.get("nom")} : {r["libelle"]}'

        async def ligne(mon: str, montant: int):
            await eco.pb.creer("eco_gains", {"joueur": joueur["id"], "commande": nom, "monnaie": mon, "montant": montant,
                                             "points_jauge": 0, "date": maintenant, "origine": ""})
        await ligne(monnaie, -prix)
        resultat = "récompense appliquée."
        n0 = n1 = 0
        joueur_maj = None
        if effet["type"] == "or":
            frais = await eco.pb.requete("GET", f'/api/collections/joueurs/records/{joueur["id"]}')
            nouveau = (frais.get("eco_or") or 0) + effet["n"]
            maj = {"eco_or": nouveau}
            if nouveau > (frais.get("eco_or_record") or 0):
                maj["eco_or_record"] = nouveau
            await eco.pb.maj("joueurs", joueur["id"], maj)
            await ligne("Or", effet["n"])
            resultat = f'+{effet["n"]} {eco_vues.or_txt(cfg)} sur votre compte.'
        elif effet["type"] == "xp":
            from app.economie import niveau_de
            frais = await eco.pb.requete("GET", f'/api/collections/joueurs/records/{joueur["id"]}')
            n0 = niveau_de(cfg["courbe"], frais.get("eco_xp") or 0)[0]
            joueur_maj = await eco.pb.maj("joueurs", joueur["id"], {"eco_xp": (frais.get("eco_xp") or 0) + effet["n"]})
            n1 = niveau_de(cfg["courbe"], joueur_maj["eco_xp"])[0]
            await ligne("XP", effet["n"])
            resultat = f'+{effet["n"]} XP sur votre compte.'
        elif effet["type"] == "role":
            resultat = f'rôle « {effet["nom"]} » obtenu.'
        elif effet["type"] == "objet":
            await eco.ajouter_objet(joueur["id"], r["objet"], 1, "rostheim", r["libelle"])
            resultat = "objet ajouté à votre inventaire."
        elif effet["type"] == "mult":
            fin = datetime.now(timezone.utc) + timedelta(days=effet["jours"])
            champ = "facteur_xp" if effet["monnaie"] == "xp" else "facteur_or"
            await eco.pb.creer("eco_multiplicateurs", {
                "libelle": f'{r["libelle"]} ({membre.display_name})', "portee": "salons" if salons else "global", "salons": salons,
                champ: effet["facteur"], "debut": maintenant, "fin": _pb_date(fin), "actif": True,
                **({"min_caracteres": effet["min_car"]} if effet.get("min_car") else {})})
            eco._charge_a = 0  # recharge la config : le multiplicateur compte tout de suite
            resultat = f'multiplicateur x{effet["facteur"]:g} activé jusqu\'au <t:{int(fin.timestamp())}:f>.'
        else:
            demande = await eco.pb.creer("ros_demandes", {
                "joueur": joueur["id"], "domaine": d["id"], "recompense": r["id"], "libelle": r["libelle"], "precision": precision,
                "prix": prix, "monnaie": monnaie, "statut": "en_attente", "message_id": ""})
            mid = await _prevenir_staff(eco, membre, d, r, precision, prix, monnaie)
            if mid:
                await eco.pb.maj("ros_demandes", demande["id"], {"message_id": mid})
            resultat = "demande transmise au staff, qui s'en occupe."
    if effet["type"] == "xp" and n1 > n0 and channel_id:
        canal = eco.client.get_channel(int(channel_id))
        if canal:
            await eco.monter_niveau(canal, membre, joueur_maj, n0, n1)
    return True, f'**{r["libelle"]}** pour {prix} {monnaie} : {resultat}'


async def _prevenir_staff(eco, membre: discord.Member, d: dict, r: dict, precision: str, prix: int, monnaie: str) -> str:
    canal = eco.client.get_channel(SALON_DEMANDES)
    if not canal:
        try:
            canal = await eco.client.fetch_channel(SALON_DEMANDES)
        except discord.HTTPException:
            log.error("Salon des demandes spéciales introuvable (%s)", SALON_DEMANDES)
            return ""
    embed = discord.Embed(title=("✨ Récompense spéciale" if r.get("speciale") else "À traiter à la main") + f' : {r["libelle"]}',
                          color=COULEUR_DEFAUT, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Joueur", value=f"{membre.mention} (`{membre.name}`)", inline=True)
    embed.add_field(name="Boutique", value=f'{d.get("nom")} · {prix} {monnaie} · {r.get("portee") or "individuel"}', inline=True)
    embed.add_field(name="Précision du joueur", value=(precision or "—")[:1024], inline=False)
    try:
        m = await canal.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return str(m.id)
    except discord.HTTPException as exc:
        log.error("Envoi de la demande spéciale impossible : %s", exc)
        return ""


# ---------------------------------------------------------------- boîte à rôle (/boite-a-role, /utiliser)
# Reprise de l'ancien ??boîte-à-rôle, en un seul message : 1. le rôle (menu des « Obtenir le rôle … » des boutiques de
# domaine), 2. le paiement de la boîte : 30 d'une monnaie de domaine (prix de la récompense « Boîte à rôle x1 »), de l'Or,
# ou une boîte de l'inventaire. Le rôle coûte en plus 10 dans la monnaie de son domaine (coût de la commande
# ??boîte-à-rôle dans ros_commandes). Rien n'est prélevé si le rôle ne peut pas être donné.

BOITE_OR = int(os.environ.get("BOITE_A_ROLE_OR", "200"))
BOITE_MONNAIE, COUT_ROLE = 30, 10  # si la base ne les donne pas
AVERTISSEMENT = "Chaque rôle coûte 10 monnaies du salon d'où il provient (aspirant érudit requiert 10 fragments de savoir)."
PAIEMENT_OR, PAIEMENT_INVENTAIRE = "or", "inv"


def est_boite_a_role(objet: dict | None) -> bool:
    return bool(objet) and normaliser(objet.get("nom")).startswith("boitearole")


async def prix_boite(eco) -> int:
    for r in await eco.pb.lister("ros_recompenses", "actif=true && toute_monnaie=true"):
        if normaliser(r.get("libelle")).startswith("boitearole"):
            return r.get("prix") or 0
    return BOITE_MONNAIE


async def _commande_boite(eco) -> dict:
    return await eco.pb.premier("ros_commandes", 'type="boite_a_role" && actif=true') or {}


def cout_role(cmd: dict) -> int:
    return cmd.get("cout_monnaie") or COUT_ROLE


async def offres_roles(eco, membre: discord.Member) -> list[tuple[dict, dict]]:
    """Rôles des boutiques de domaine (« Obtenir le rôle … ») que le membre n'a pas encore, avec leur domaine."""
    offres = []
    for d in await domaines(eco):
        for r in await eco.pb.lister("ros_recompenses", f'actif=true && domaine="{echapper(d["id"])}"', tri="ordre"):
            effet = analyser(r)
            if effet["type"] == "role" and lunes.disponible(r) and not eco._a_le_role(membre, None, effet["nom"]):
                offres.append((d, r))
    return offres


async def _boites(eco, joueur: dict | None) -> list[tuple[dict, int]]:
    """Boîtes à rôle de l'inventaire du joueur : (objet, quantité)."""
    from app.eco_actions import quantite
    if not joueur:
        return []
    res = []
    for o in await eco.pb.lister("eco_objets", "actif=true"):
        if est_boite_a_role(o):
            n = (await quantite(eco, joueur["id"], o["id"]))[1]
            if n > 0:
                res.append((o, n))
    return res


async def vue_boite(eco, membre: discord.Member, rec_id: str | None = None) -> dict:
    """Conseiller des Rôles (Components V2) : choix du rôle, puis du paiement de la boîte (débloqué une fois le rôle choisi)."""
    offres = await offres_roles(eco, membre)
    if not offres:
        return eco_vues.erreur_v2("Aucun rôle à choisir : vous les avez déjà tous, ou l'événement Rostheim n'est pas actif.")
    cfg = await eco.config()
    cmd = await _commande_boite(eco)
    prix, cout = await prix_boite(eco), cout_role(cmd)
    joueur = await eco.joueur_de(membre)
    soldes = {}
    for d in await domaines(eco):
        s = await solde(eco, joueur, d)
        soldes[d["id"]] = (s or {}).get("monnaie") or 0
    or_ = (joueur or {}).get("eco_or") or 0
    nb_boites = sum(n for _o, n in await _boites(eco, joueur))
    choisi = next(((d, r) for d, r in offres if r["id"] == rec_id), None)
    avertissement = (cmd.get("commentaires") or "").strip() or AVERTISSEMENT

    paiements = [f'**{prix}** {d.get("monnaie_nom")}' for d in await domaines(eco)]
    entete = ("-# Conseiller des Rôles\n## Que préférez-vous ?\n"
              f"Une boîte à rôle se paie au choix : {' · '.join(paiements)} · **{BOITE_OR}** {eco_vues.or_txt(cfg)} · "
              f"ou une boîte de votre inventaire.\n-# ⚠️ {avertissement}")
    blocs = [eco_vues._texte(entete), {"type": 14, "divider": True, "spacing": 1}]

    roles = [{"label": analyser(r)["nom"][:100], "value": r["id"], "default": bool(choisi) and r["id"] == choisi[1]["id"],
              "description": f'+{cout} {d.get("monnaie_nom")} ({d.get("nom")}) · vous en avez {soldes.get(d["id"], 0)}'[:100]}
             for d, r in offres[:25]]
    blocs.append(eco_vues._texte("**1. Votre rôle**"))
    blocs.append({"type": 1, "components": [{"type": 3, "custom_id": f"eco:bxr:{membre.id}", "placeholder": "Choisissez votre rôle…", "options": roles}]})

    options = [{"label": f'{prix} {d.get("monnaie_nom")}'[:100], "value": d["id"], "description": f'{d.get("nom")} · vous en avez {soldes.get(d["id"], 0)}'[:100]}
               for d in await domaines(eco)]
    options.append({"label": f"{BOITE_OR} Or", "value": PAIEMENT_OR, "description": f"vous en avez {or_}"})
    options.append({"label": "Boîte de l'inventaire", "value": PAIEMENT_INVENTAIRE, "description": f"vous en avez {nb_boites}"})
    blocs.append(eco_vues._texte("**2. Payer la boîte avec…**"))
    blocs.append({"type": 1, "components": [{
        "type": 3, "custom_id": f'eco:bxp:{membre.id}:{choisi[1]["id"] if choisi else "-"}', "options": options[:25], "disabled": not choisi,
        "placeholder": (f'Payer la boîte pour « {analyser(choisi[1])["nom"]} »…' if choisi else "Choisissez d'abord votre rôle")[:150]}]})
    return {"flags": eco_vues.V2 | eco_vues.PRIVE, "components": [{"type": 17, "accent_color": eco_vues.OR_DEFAUT, "components": blocs}]}


async def _gain(eco, joueur: dict, monnaie: str, montant: int, note: str):
    await eco.pb.creer("eco_gains", {"joueur": joueur["id"], "commande": f"boîte à rôle : {note}", "monnaie": monnaie, "montant": montant,
                                     "points_jauge": 0, "date": _pb_date(datetime.now(timezone.utc)), "origine": ""})


async def ouvrir_boite(eco, membre: discord.Member, rec_id: str, paiement: str = PAIEMENT_INVENTAIRE) -> tuple[bool, str]:
    """Vérifie le paiement de la boîte et le coût du rôle, donne le rôle, puis seulement prélève."""
    from app.eco_actions import changer_or, retirer_objet
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return False, "Aucune fiche joueur liée à ce compte Discord."
    tous = await domaines(eco)
    r = await eco.pb.premier("ros_recompenses", f'id="{echapper(rec_id)}" && actif=true')
    dr = next((x for x in tous if r and x["id"] == r.get("domaine")), None)
    if not r or not dr or analyser(r)["type"] != "role":
        return False, "Ce rôle n'est plus proposé."
    nom = analyser(r)["nom"]
    if eco._a_le_role(membre, None, nom):
        return False, f"Vous avez déjà le rôle « {nom} » : rien n'a été prélevé."
    cfg = await eco.config()
    prix, cout = await prix_boite(eco), cout_role(await _commande_boite(eco))
    m_role = dr.get("monnaie_nom") or "monnaie"
    async with eco._verrou(membre.id):
        sd_role = await solde(eco, joueur, dr)
        boite = None
        if paiement == PAIEMENT_INVENTAIRE:
            boite = next(iter(await _boites(eco, joueur)), (None, 0))[0]
            if not boite:
                return False, "Vous n'avez pas de Boîte à rôle dans votre inventaire."
            payee = "une boîte de l'inventaire"
        elif paiement == PAIEMENT_OR:
            frais = await eco.pb.requete("GET", f'/api/collections/joueurs/records/{joueur["id"]}')
            if (frais.get("eco_or") or 0) < BOITE_OR:
                return False, f'Il faut {BOITE_OR} {eco_vues.or_txt(cfg)} pour la boîte (vous en avez {frais.get("eco_or") or 0}).'
            payee = f"{BOITE_OR} {eco_vues.or_txt(cfg)}"
        else:
            dp = next((x for x in tous if x["id"] == paiement), None)
            if not dp:
                return False, "Cette monnaie n'est pas ouverte."
            sd = await solde(eco, joueur, dp)
            besoin = prix + (cout if dp["id"] == dr["id"] else 0)
            if (sd or {}).get("monnaie", 0) < besoin:
                detail = f" ({prix} pour la boîte + {cout} pour le rôle)" if dp["id"] == dr["id"] else " pour la boîte"
                return False, f'Il faut {besoin} {dp.get("monnaie_nom")}{detail} : vous en avez {(sd or {}).get("monnaie") or 0}.'
            payee = f'{prix} {dp.get("monnaie_nom")}'
        if (sd_role or {}).get("monnaie", 0) < cout:
            return False, f'Le rôle « {nom} » coûte {cout} {m_role} : vous en avez {(sd_role or {}).get("monnaie") or 0}. Rien n\'a été prélevé.'
        echec = await eco.donner_role(membre, joueur, None, nom, 0)
        if echec:
            return False, f"Rôle non obtenu, rien n'a été prélevé : {echec}."
        if boite:
            await retirer_objet(eco, joueur["id"], boite["id"], 1, "utilisation", f"boîte à rôle : {r['libelle']}")
        elif paiement == PAIEMENT_OR:
            await changer_or(eco, joueur["id"], -BOITE_OR)
            await _gain(eco, joueur, "Or", -BOITE_OR, r["libelle"])
        else:
            sd = await solde(eco, joueur, dp)
            await eco.pb.maj("ros_soldes", sd["id"], {"monnaie": sd["monnaie"] - prix})
            await _gain(eco, joueur, dp.get("monnaie_nom") or "", -prix, r["libelle"])
        sd_role = await solde(eco, joueur, dr)
        await eco.pb.maj("ros_soldes", sd_role["id"], {"monnaie": sd_role["monnaie"] - cout})
        await _gain(eco, joueur, m_role, -cout, r["libelle"])
    return True, f"{membre.mention} consomme une **Boîte à rôle** ({payee}) : rôle **{nom}** obtenu pour {cout} {m_role}."
