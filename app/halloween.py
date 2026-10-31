"""Boutique d'Halloween : une monnaie à part, les bonbons, gagnés en écrivant sur le serveur (au hasard) ou donnés
par les gérants, et dépensés dans une boutique dont tout se règle sur la page « Halloween » du site (Kyanite et K).

Collections (créées par scripts/setup-halloween.mjs du site) :
- hal_reglages (cle « general ») : ouverte, titre, description, couleur, monnaie_nom, emojis (citrouille, fantôme,
  bonbon : emoji Unicode ou emoji du serveur « <:nom:id> »), gains par message (gains_actifs, gain_chance_pct,
  gain_min, gain_max, gain_cooldown_s, gain_salons, reaction_gain), admin_discord_ids (gérants côté Discord),
  annonce_hasard (résultat d'un objet mystère montré dans le salon)
- hal_articles : nom, emoji, description, prix (bonbons), type (objet, role, role_temporaire, hasard, or, xp, demande),
  objet, quantite, role_nom, role_id, duree_jours, stock, stock_illimite, limite_par_joueur, actif, ordre
- hal_lots : lots d'un article « hasard » (objet mystère) : libelle, type (objet, role, role_temporaire, or, xp,
  bonbons, rien), objet, quantite, role_nom, role_id, duree_jours, poids (chance relative), actif
- hal_soldes : bonbons de chaque joueur (joueur, bonbons, total_gagne)
- hal_mouvements : historique (type gain, achat, don, retrait ; montant signé ; article ; libelle ; resultat)

Sur Discord : /halloween (boutique), /bonbons (solde et classement), /halloween-admin (donner, retirer, vitrine) et
la vitrine, un message public à trois boutons 🎃 Boutique · 👻 Objets mystère · 🍬 Mes bonbons. La boutique apparaît
aussi dans le menu de /boutique quand elle est ouverte. Les custom_id sont « eco:hal:… » (app/eco_interactions.py).
Comme partout dans l'économie, un rôle est donné AVANT de débiter : s'il ne peut pas l'être, rien n'est prélevé.
"""
import logging
import random
import re
import time
from datetime import datetime, timezone

import discord

from app import eco_vues
from app.eco_cartes import abrege
from app.economie import meme_nom
from app.pocketbase import echapper

log = logging.getLogger("cdt_scrib.halloween")

PREFIXE = "h_"  # valeur de la boutique d'Halloween dans le menu de /boutique
VALEUR_MENU = PREFIXE + "halloween"
PAR_PAGE = 5
ORANGE = 0xE67E22
RECHARGE_S = 60
TYPES_ROLE = ("role", "role_temporaire")
DEFAUTS = {
    "ouverte": False, "titre": "Boutique d'Halloween", "description": "", "couleur": "#e67e22", "monnaie_nom": "Bonbons",
    "emoji_citrouille": "🎃", "emoji_fantome": "👻", "emoji_bonbon": "🍬",
    "gains_actifs": False, "gain_chance_pct": 20, "gain_min": 1, "gain_max": 3, "gain_cooldown_s": 60, "gain_salons": "",
    "reaction_gain": True, "admin_discord_ids": "", "annonce_hasard": True,
}

_cache: dict = {"a": 0.0, "r": None}
_derniers_gains: dict[int, float] = {}


def _pb_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")


# ---------------------------------------------------------------- réglages

async def reglages(eco, frais: bool = False) -> dict:
    """Réglages de la boutique (valeurs par défaut pour les champs vides), relus au plus toutes les minutes."""
    if not frais and _cache["r"] is not None and time.time() - _cache["a"] < RECHARGE_S:
        return _cache["r"]
    try:
        r = await eco.pb.premier("hal_reglages", 'cle="general"') or {}
    except Exception:  # collection absente (script du site pas encore lancé) : boutique fermée
        log.warning("Réglages d'Halloween illisibles (collection hal_reglages absente ?)")
        r = {}
    complet = dict(DEFAUTS)
    for k, v in r.items():
        if v not in (None, "") or isinstance(v, bool):
            complet[k] = v
    _cache.update(a=time.time(), r=complet)
    return complet


def oublier_cache():
    _cache.update(a=0.0, r=None)


def emoji_composant(texte: str | None) -> dict | None:
    """Emoji d'un bouton ou d'une option : Unicode (« 🎃 ») ou emoji du serveur (« <:citrouille:123> »)."""
    texte = (texte or "").strip()
    if not texte:
        return None
    m = re.fullmatch(r"<(a?):(\w+):(\d+)>", texte)
    if m:
        return {"id": m[3], "name": m[2], "animated": bool(m[1])}
    return {"name": texte}


def citrouille(r: dict) -> str:
    return r.get("emoji_citrouille") or "🎃"


def fantome(r: dict) -> str:
    return r.get("emoji_fantome") or "👻"


def bonbon(r: dict) -> str:
    return r.get("emoji_bonbon") or "🍬"


def monnaie(r: dict, n: int | None = None) -> str:
    """« 12 🍬 Bonbons » (ou le seul nom sans nombre)."""
    nom = r.get("monnaie_nom") or "Bonbons"
    return f"{eco_vues._n(n)} {bonbon(r)} {nom}" if n is not None else f"{bonbon(r)} {nom}"


def _ids(texte) -> set[str]:
    return {x.strip() for x in str(texte or "").replace(";", ",").replace(" ", ",").split(",") if x.strip()}


async def est_admin(eco, membre: discord.Member) -> bool:
    """Gérants côté Discord : administrateurs du serveur, IDs de la page Halloween (Kyanite) et admins de loterie."""
    from app.loteries import LOTERIE_ADMINS
    if getattr(getattr(membre, "guild_permissions", None), "administrator", False):
        return True
    r = await reglages(eco)
    return str(membre.id) in _ids(r.get("admin_discord_ids")) | LOTERIE_ADMINS


# ---------------------------------------------------------------- soldes

async def solde(eco, joueur_id: str) -> dict | None:
    return await eco.pb.premier("hal_soldes", f'joueur="{echapper(joueur_id)}"')


async def bonbons_de(eco, joueur: dict | None) -> int:
    if not joueur:
        return 0
    s = await solde(eco, joueur["id"])
    return (s or {}).get("bonbons") or 0


async def changer_bonbons(eco, joueur_id: str, delta: int, type_: str, libelle: str, origine: str = "",
                          article: str = "", resultat: str = "") -> int:
    """Ajoute (ou retire) des bonbons, trace le mouvement, et renvoie le nouveau solde (jamais négatif)."""
    s = await solde(eco, joueur_id)
    avant = (s or {}).get("bonbons") or 0
    apres = max(0, avant + delta)
    gagne = (s or {}).get("total_gagne") or 0
    if delta > 0 and type_ in ("gain", "don", "lot"):
        gagne += delta
    if s:
        await eco.pb.maj("hal_soldes", s["id"], {"bonbons": apres, "total_gagne": gagne})
    else:
        await eco.pb.creer("hal_soldes", {"joueur": joueur_id, "bonbons": apres, "total_gagne": gagne})
    try:
        await eco.pb.creer("hal_mouvements", {"joueur": joueur_id, "type": type_, "montant": apres - avant, "article": article,
                                              "libelle": libelle[:300], "resultat": resultat[:500], "date": _pb_date(), "origine": origine})
        r = await reglages(eco)
        await eco.pb.creer("eco_gains", {"joueur": joueur_id, "commande": f"halloween : {libelle}"[:300], "monnaie": r.get("monnaie_nom") or "Bonbons",
                                         "montant": apres - avant, "points_jauge": 0, "date": _pb_date(), "origine": origine})
    except Exception:  # la trace ne doit jamais bloquer
        log.exception("Trace d'un mouvement de bonbons impossible")
    return apres


# ---------------------------------------------------------------- gains par message

def _salon_eligible(eco, cfg: dict, r: dict, channel) -> bool:
    """Salons de la page Halloween (noms ou IDs) ; à défaut, les salons éligibles de l'économie."""
    liste = [x.strip().lstrip("#") for x in re.split(r"[,;\n]", r.get("gain_salons") or "") if x.strip()]
    if not liste:
        return eco._salon(cfg, channel) is not None
    salons = [channel] + ([channel.parent] if isinstance(channel, discord.Thread) and channel.parent else [])
    return any(str(s.id) == x or meme_nom(getattr(s, "name", ""), x) for s in salons for x in liste)


async def gain_message(eco, message: discord.Message, rng: random.Random | None = None) -> int:
    """Un message peut rapporter des bonbons (chance réglée sur le site) ; renvoie le nombre gagné."""
    r = await reglages(eco)
    if not r.get("gains_actifs"):
        return 0
    membre = message.author
    maintenant = time.time()
    if maintenant - _derniers_gains.get(membre.id, 0) < (r.get("gain_cooldown_s") or 0):
        return 0
    cfg = await eco.config()
    if not _salon_eligible(eco, cfg, r, message.channel):
        return 0
    rng = rng or random
    if rng.random() * 100 >= (r.get("gain_chance_pct") or 0):
        return 0
    mini = max(0, int(r.get("gain_min") or 0))
    n = rng.randint(mini, max(mini, int(r.get("gain_max") or 0)))
    if n <= 0:
        return 0
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return 0
    _derniers_gains[membre.id] = maintenant
    async with eco._verrou(membre.id):
        await changer_bonbons(eco, joueur["id"], n, "gain", f'message #{getattr(message.channel, "name", "?")}', message.jump_url)
    if r.get("reaction_gain"):
        try:
            await message.add_reaction(bonbon(r))
        except (discord.HTTPException, TypeError, ValueError):
            pass
    return n


# ---------------------------------------------------------------- catalogue

async def articles(eco, filtre: str = "tout") -> list[dict]:
    liste = await eco.pb.lister("hal_articles", "actif=true", tri="ordre")
    if filtre == "mystere":
        liste = [a for a in liste if a.get("type") == "hasard"]
    return liste


async def lots(eco, article_id: str) -> list[dict]:
    return [l for l in await eco.pb.lister("hal_lots", f'article="{echapper(article_id)}" && actif=true') if (l.get("poids") or 0) > 0]


def tirer(liste: list[dict], rng: random.Random | None = None) -> dict | None:
    """Tirage pondéré par « poids »."""
    total = sum(l.get("poids") or 0 for l in liste)
    if total <= 0:
        return None
    x = (rng or random).random() * total
    for l in liste:
        x -= l.get("poids") or 0
        if x < 0:
            return l
    return liste[-1]


def _duree_txt(jours) -> str:
    return f"{jours:g} jour{'s' if jours > 1 else ''}" if jours else ""


def description_effet(e: dict, r: dict, objets: dict | None = None) -> str:
    """Ce que rapporte un article ou un lot, en clair."""
    t, q = e.get("type"), max(1, int(e.get("quantite") or 1))
    if t == "objet":
        o = (objets or {}).get(e.get("objet")) or {}
        return f'{q} × {o.get("emoji") or ""} {o.get("nom") or "objet"}'.replace("  ", " ")
    if t in TYPES_ROLE:
        duree = _duree_txt(e.get("duree_jours")) if t == "role_temporaire" else ""
        return f'rôle « {e.get("role_nom") or e.get("role_id")} »' + (f" pendant {duree}" if duree else "")
    if t == "or":
        return f"{eco_vues._n(q)} Or"
    if t == "xp":
        return f"{eco_vues._n(q)} XP"
    if t == "bonbons":
        return monnaie(r, q)
    if t == "rien":
        return "rien du tout… " + fantome(r)
    if t == "demande":
        return "une récompense remise par les gérants"
    return e.get("libelle") or "?"


# ---------------------------------------------------------------- effets et achat

async def appliquer(eco, membre: discord.Member, joueur: dict, e: dict, libelle: str, salon=None) -> str | None:
    """Applique un effet (article ou lot). Renvoie None si c'est fait, sinon la raison de l'échec (rien n'a changé).
    Seul un rôle peut échouer : il est donc donné en premier par l'appelant."""
    from app.eco_actions import _xp, changer_or, _gain
    t, q = e.get("type"), max(1, int(e.get("quantite") or 1))
    if t in TYPES_ROLE:
        jours = (e.get("duree_jours") or 0) if t == "role_temporaire" else 0
        return await eco.donner_role(membre, joueur, e.get("role_id") or None, e.get("role_nom") or None, jours, origine=f"Halloween : {libelle}")
    if t == "objet" and e.get("objet"):
        await eco.ajouter_objet(joueur["id"], e["objet"], q, "achat", f"Halloween : {libelle}")
    elif t == "or":
        await changer_or(eco, joueur["id"], q)
        await _gain(eco, joueur["id"], f"halloween : {libelle}", "Or", q, "")
    elif t == "xp":
        await _xp(eco, membre, joueur["id"], q, f"Halloween : {libelle}", salon)
    elif t == "bonbons":
        await changer_bonbons(eco, joueur["id"], q, "lot", libelle)
    elif t == "demande":
        await _prevenir_gerants(eco, membre, libelle)
    return None


def _a_le_role(eco, membre, e: dict) -> bool:
    if e.get("type") not in TYPES_ROLE:
        return False
    if e.get("type") == "role_temporaire":
        return False  # un rôle temporaire déjà détenu est prolongé
    role = eco._role_discord(membre.guild, e.get("role_id") or None, e.get("role_nom") or None)
    return bool(role and role in membre.roles)


async def deja_achete(eco, joueur_id: str, article_id: str) -> int:
    lignes = await eco.pb.lister("hal_mouvements", f'joueur="{echapper(joueur_id)}" && article="{echapper(article_id)}" && type="achat"')
    return len(lignes)


async def acheter(eco, membre: discord.Member, article_id: str, origine: str = "", salon=None,
                  rng: random.Random | None = None) -> tuple[bool, str, str]:
    """Achat d'un article : (réussi, texte privé, annonce publique éventuelle)."""
    r = await reglages(eco, frais=True)
    if not r.get("ouverte"):
        return False, f"{citrouille(r)} La boutique d'Halloween est fermée.", ""
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return False, "Aucune fiche joueur liée à ce compte Discord.", ""
    async with eco._verrou(membre.id):
        try:
            a = await eco.pb.requete("GET", f"/api/collections/hal_articles/records/{article_id}")
        except Exception:
            a = None
        if not a or not a.get("actif"):
            return False, "Cet article n'est plus en vente.", ""
        prix = max(0, int(a.get("prix") or 0))
        if not a.get("stock_illimite") and (a.get("stock") or 0) <= 0:
            return False, "Cet article est épuisé.", ""
        limite = int(a.get("limite_par_joueur") or 0)
        if limite and await deja_achete(eco, joueur["id"], a["id"]) >= limite:
            return False, f"Tu as déjà acheté **{a['nom']}** le nombre de fois permis ({limite}).", ""
        avoir = await bonbons_de(eco, joueur)
        if avoir < prix:
            return False, f"Il te manque {monnaie(r, prix - avoir)} (tu en as {eco_vues._n(avoir)}).", ""
        if _a_le_role(eco, membre, a):
            return False, f"Tu as déjà le rôle « {a.get('role_nom') or a.get('role_id')} » : rien n'a été prélevé.", ""
        effet, lot = a, None
        if a.get("type") == "hasard":
            possibles = [l for l in await lots(eco, a["id"]) if not _a_le_role(eco, membre, l)]
            lot = tirer(possibles, rng)
            if not lot:
                return False, f"{fantome(r)} Cet objet mystère est vide pour l'instant : rien n'a été prélevé.", ""
            effet = lot
        objets = {}
        if effet.get("type") == "objet" and effet.get("objet"):
            try:
                o = await eco.pb.requete("GET", f'/api/collections/eco_objets/records/{effet["objet"]}')
                objets = {o["id"]: o}
            except Exception:
                return False, "L'objet de cet article n'existe plus : rien n'a été prélevé.", ""
        echec = await appliquer(eco, membre, joueur, effet, a["nom"], salon)
        if echec:
            return False, f"Achat annulé, rien n'a été prélevé : {echec}.", ""
        gain = description_effet(effet, r, objets)
        reste = await changer_bonbons(eco, joueur["id"], -prix, "achat", a["nom"], origine, article=a["id"], resultat=gain)
        if not a.get("stock_illimite"):
            frais = await eco.pb.requete("GET", f"/api/collections/hal_articles/records/{a['id']}")
            await eco.pb.maj("hal_articles", a["id"], {"stock": max(0, (frais.get("stock") or 0) - 1)})
    titre = f'{a.get("emoji") or ""} **{a["nom"]}**'.strip()
    if lot:
        texte = f"{fantome(r)} Tu ouvres {titre} pour {monnaie(r, prix)}… et tu obtiens **{gain}** !"
        annonce = f"{fantome(r)} {membre.mention} ouvre {titre} et obtient **{gain}** !" if r.get("annonce_hasard") else ""
    else:
        texte = f"{citrouille(r)} {titre} pour {monnaie(r, prix)} : {gain}."
        annonce = ""
    if effet.get("type") == "demande":
        texte += " Les gérants sont prévenus et te la remettront."
    return True, f"{texte}\nIl te reste {monnaie(r, reste)}.", annonce


async def _prevenir_gerants(eco, membre: discord.Member, libelle: str):
    r = await reglages(eco)
    for uid in _ids(r.get("admin_discord_ids")):
        if not uid.isdigit():
            continue
        try:
            user = eco.client.get_user(int(uid)) or await eco.client.fetch_user(int(uid))
            await user.send(f"{citrouille(r)} {membre.mention} (`{membre.name}`) a acheté **{libelle}** dans la boutique d'Halloween : "
                            "c'est une récompense à lui remettre à la main.")
        except discord.HTTPException:
            log.warning("Impossible de prévenir le gérant %s (messages privés fermés ?)", uid)


async def donner(eco, auteur: discord.Member, cible: discord.Member, n: int, motif: str = "") -> tuple[bool, str]:
    """/halloween-admin donner|retirer : n positif = don, négatif = retrait."""
    if not await est_admin(eco, auteur):
        return False, "Réservé aux gérants de la boutique d'Halloween."
    if not n:
        return False, "Le nombre doit être différent de 0."
    joueur = await eco.joueur_de(cible)
    if not joueur:
        return False, f"{cible.mention} n'a pas de fiche joueur liée à son compte Discord."
    r = await reglages(eco)
    libelle = (motif or ("don" if n > 0 else "retrait")) + f" (par {auteur.display_name})"
    async with eco._verrou(cible.id):
        reste = await changer_bonbons(eco, joueur["id"], n, "don" if n > 0 else "retrait", libelle)
    verbe = f"reçoit {monnaie(r, n)}" if n > 0 else f"perd {monnaie(r, -n)}"
    return True, f"{cible.mention} {verbe}{f' ({motif})' if motif else ''}. Solde : {monnaie(r, reste)}."


# ---------------------------------------------------------------- vues (Components V2)

def _cadre(r: dict, blocs: list, prive: bool = True) -> dict:
    return {"flags": eco_vues.V2 | (eco_vues.PRIVE if prive else 0),
            "components": [{"type": 17, "accent_color": eco_vues._couleur(r.get("couleur")) if r.get("couleur") else ORANGE, "components": blocs}]}


async def fermee(eco) -> dict:
    r = await reglages(eco)
    return _cadre(r, [eco_vues._texte(f"{citrouille(r)} La boutique d'Halloween est fermée pour le moment. {fantome(r)}")])


async def vue(eco, membre, page: int = 0, filtre: str = "tout") -> dict:
    """Boutique : en-tête 🎃 👻, articles (bouton « 🍬 prix - Acheter »), pages, solde de bonbons."""
    r = await reglages(eco)
    if not r.get("ouverte"):
        return await fermee(eco)
    liste = await articles(eco, filtre)
    pages = max(1, -(-len(liste) // PAR_PAGE))
    page = max(0, min(page, pages - 1))
    joueur = await eco.joueur_de(membre)
    objets = {o["id"]: o for o in await eco.pb.lister("eco_objets")} if liste else {}
    titre = r.get("titre") or DEFAUTS["titre"]
    entete = f"## {citrouille(r)} {titre} {fantome(r)}"
    if filtre == "mystere":
        entete += f"\n{fantome(r)} **Objets mystère** : on ne sait jamais ce qu'il y a dedans…"
    if r.get("description"):
        entete += f"\n{r['description']}"
    blocs = [eco_vues._texte(entete[:1800]), {"type": 14, "divider": True, "spacing": 1}]
    emoji_b = emoji_composant(bonbon(r))
    for a in liste[page * PAR_PAGE:(page + 1) * PAR_PAGE]:
        epuise = not a.get("stock_illimite") and (a.get("stock") or 0) <= 0
        mystere = a.get("type") == "hasard"
        lignes = [f'### {a.get("emoji") or (fantome(r) if mystere else citrouille(r))} {a["nom"]}']
        notes = []
        if mystere:
            contenu = await lots(eco, a["id"])
            notes.append(f"{fantome(r)} Objet mystère : {len(contenu)} surprise{'s' if len(contenu) > 1 else ''} possible{'s' if len(contenu) > 1 else ''}")
        else:
            notes.append(description_effet(a, r, objets))
        if not a.get("stock_illimite"):
            notes.append("❌ Épuisé" if epuise else f"Reste : {a.get('stock')}")
        if a.get("limite_par_joueur"):
            notes.append(f"{a['limite_par_joueur']} par joueur")
        lignes.append("-# " + " · ".join(notes))
        if a.get("description"):
            lignes.append(a["description"])
        blocs.append({"type": 9, "components": [eco_vues._texte("\n".join(lignes)[:1500])],
                      "accessory": eco_vues._bouton(f'{eco_vues._n(a.get("prix"))} - Acheter', f'eco:hal:buy:{a["id"]}', style=1,
                                                    disabled=epuise, emoji=emoji_b)})
    if not liste:
        blocs.append(eco_vues._texte("*Aucun objet mystère pour le moment.*" if filtre == "mystere" else "*Rien en vitrine pour le moment.*"))
    blocs.append({"type": 14, "divider": True, "spacing": 1})
    blocs.append(eco_vues._texte(f"-# Tu as {abrege(await bonbons_de(eco, joueur))} {bonbon(r)} {r.get('monnaie_nom') or 'Bonbons'}"
                                 if joueur else "-# Aucune fiche joueur liée à ton compte Discord."))
    blocs.append({"type": 1, "components": [
        eco_vues._bouton("Précédent", f"eco:hal:pg:{page - 1}:{filtre}", disabled=page == 0),
        eco_vues._bouton(f"Page {page + 1}/{pages}", "eco:rien", disabled=True),
        eco_vues._bouton("Suivant", f"eco:hal:pg:{page + 1}:{filtre}", style=1, disabled=page >= pages - 1),
        eco_vues._bouton("Fermer", "eco:fermer"),
    ]})
    autre = ("tout", "Toute la boutique", citrouille(r)) if filtre == "mystere" else ("mystere", "Objets mystère", fantome(r))
    blocs.append({"type": 1, "components": [
        eco_vues._bouton(autre[1], f"eco:hal:pg:0:{autre[0]}", emoji=emoji_composant(autre[2])),
        eco_vues._bouton("Mes bonbons", "eco:hal:sol", emoji=emoji_b),
    ]})
    return _cadre(r, blocs)


async def vue_solde(eco, membre, maj: bool = False) -> dict:
    """Solde du joueur et les 10 plus gros sacs de bonbons du serveur."""
    r = await reglages(eco)
    joueur = await eco.joueur_de(membre)
    lignes = [f"## {bonbon(r)} Tes {r.get('monnaie_nom') or 'Bonbons'}"]
    if joueur:
        s = await solde(eco, joueur["id"]) or {}
        lignes.append(f"Tu as **{monnaie(r, s.get('bonbons') or 0)}** (gagnés en tout : {eco_vues._n(s.get('total_gagne') or 0)}).")
    else:
        lignes.append("Aucune fiche joueur liée à ton compte Discord.")
    soldes = sorted((s for s in await eco.pb.lister("hal_soldes", expand="joueur") if (s.get("bonbons") or 0) > 0),
                    key=lambda s: -(s.get("bonbons") or 0))[:10]
    if soldes:
        lignes.append(f"\n**{citrouille(r)} Les plus gros sacs**")
        for i, s in enumerate(soldes, 1):
            j = (s.get("expand") or {}).get("joueur") or {}
            qui = f'<@{j["discord_id"]}>' if str(j.get("discord_id") or "").isdigit() else (j.get("pseudo") or "?")
            lignes.append(f"{i}. {qui} : {eco_vues._n(s.get('bonbons'))} {bonbon(r)}")
    if r.get("gains_actifs"):
        lignes.append(f"\n-# Chaque message dans les salons ouverts peut rapporter des {bonbon(r)}.")
    blocs = [eco_vues._texte("\n".join(lignes)[:3800])]
    if r.get("ouverte"):
        blocs.append({"type": 1, "components": [eco_vues._bouton("Boutique", "eco:hal:pg:0:tout", style=1, emoji=emoji_composant(citrouille(r)))]})
    return _cadre(r, blocs)


async def solde_de(eco, cible) -> str:
    """/halloween-admin solde : bonbons d'un membre, pour les gérants."""
    r = await reglages(eco)
    joueur = await eco.joueur_de(cible)
    if not joueur:
        return f"{cible.mention} n'a pas de fiche joueur liée à son compte Discord."
    s = await solde(eco, joueur["id"]) or {}
    return f"{cible.mention} a {monnaie(r, s.get('bonbons') or 0)} (gagnés en tout : {eco_vues._n(s.get('total_gagne') or 0)})."


async def vitrine(eco) -> dict:
    """Message public posté par un gérant : trois boutons 🎃 👻 🍬 qui ouvrent la boutique en privé."""
    r = await reglages(eco, frais=True)
    titre = r.get("titre") or DEFAUTS["titre"]
    texte = (f"# {citrouille(r)} {titre} {fantome(r)}\n"
             + (f"{r['description']}\n" if r.get("description") else "")
             + f"Ici, tout se paie en **{bonbon(r)} {r.get('monnaie_nom') or 'Bonbons'}**."
             + (f"\n-# Écrivez sur le serveur : vos messages peuvent rapporter des {bonbon(r)} !" if r.get("gains_actifs") else ""))
    return _cadre(r, [eco_vues._texte(texte[:3800]), {"type": 1, "components": [
        eco_vues._bouton("Boutique", "eco:hal:v:tout", style=1, emoji=emoji_composant(citrouille(r))),
        eco_vues._bouton("Objets mystère", "eco:hal:v:mystere", style=2, emoji=emoji_composant(fantome(r))),
        eco_vues._bouton("Mes bonbons", "eco:hal:v:sol", style=2, emoji=emoji_composant(bonbon(r))),
    ]}], prive=False)


async def option_menu(eco) -> dict | None:
    """Ligne de la boutique d'Halloween dans le menu de /boutique (quand elle est ouverte)."""
    r = await reglages(eco)
    if not r.get("ouverte"):
        return None
    o = {"label": (r.get("titre") or DEFAUTS["titre"])[:100], "value": VALEUR_MENU, "description": f"Payée en {r.get('monnaie_nom') or 'Bonbons'}"[:100]}
    e = emoji_composant(citrouille(r))
    if e:
        o["emoji"] = e
    return o
