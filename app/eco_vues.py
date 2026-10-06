"""Affichage de l'économie (format JSON de l'API Discord), dans l'esprit des écrans de Draftbot :
- boutique au format « Components V2 » : choix de la boutique, puis un cadre aux couleurs de la boutique avec,
  pour chaque article, titre, quantité restante, description et un bouton « 🪙 prix - Acheter » ; pages, tri,
  changement de boutique ;
- classements (Or, niveaux) paginés, inventaire en grille, cartes image (/argent, /niveau : app/eco_cartes.py).
Les custom_id « eco:… » sont traités dans app/eco_interactions.py.
"""
from datetime import datetime, timezone

import discord

from app.eco_cartes import abrege
from app.pocketbase import echapper

OR_DEFAUT = 0xC5A24F
VERT, ROUGE, GRIS = 0x3BA55C, 0xC0392B, 0x99AAB5
V2, PRIVE = 1 << 15, 1 << 6
PAR_PAGE_BOUTIQUE, PAR_PAGE_TOP, PAR_PAGE_INV = 5, 5, 24
TRIS = [("prix_asc", "Prix croissant"), ("prix_desc", "Prix décroissant"), ("nom", "Nom"), ("ordre", "Ordre de la boutique")]


def _n(v) -> str:
    return f"{int(v or 0):,}".replace(",", " ")


def _couleur(hexa: str | None) -> int:
    try:
        return int(hexa.lstrip("#"), 16) if hexa and hexa.startswith("#") else OR_DEFAUT
    except ValueError:
        return OR_DEFAUT


def or_id(cfg: dict) -> str:
    eid = (cfg["reglages"].get("or_emoji_id") or "").strip()
    return eid if eid.isdigit() else ""


def or_txt(cfg: dict) -> str:
    """Emoji d'Or du serveur (ID enregistré dans les réglages), sinon le mot « Or »."""
    return f"<:or:{or_id(cfg)}>" if or_id(cfg) else "Or"


def xp_txt(cfg: dict) -> str:
    return f'{cfg["reglages"].get("xp_emoji") or ""} {cfg["reglages"].get("xp_nom") or "XP"}'.strip()


def erreur(texte: str) -> dict:
    return {"description": texte, "color": ROUGE}


def resultat_achat(ok: bool, texte: str) -> dict:
    return {"description": texte if not ok or texte.startswith("❌") else f"✅ Achat effectué : {texte}", "color": VERT if ok else ROUGE}


def resultat(ok: bool, texte: str) -> dict:
    return {"description": texte, "color": VERT if ok else ROUGE}


def _texte(contenu: str) -> dict:
    return {"type": 10, "content": contenu}


def _bouton(label: str, custom_id: str, style: int = 2, disabled: bool = False, emoji: dict | None = None) -> dict:
    b = {"type": 2, "style": style, "label": label, "custom_id": custom_id, "disabled": disabled}
    if emoji:
        b["emoji"] = emoji
    return b


# ---------------------------------------------------------------- boutique (Components V2)

def _acces_txt(b: dict) -> str:
    roles = [r.strip().lstrip("@") for r in (b.get("roles_autorises") or "").split(",") if r.strip()]
    if not roles:
        return ""
    txt = "🔒 Réservée au rôle " + ", ".join(roles)
    return txt + (" (retiré à l'achat)" if b.get("retirer_roles_acces") else "")


async def vue_choix_boutique(eco, membre) -> dict:
    """Premier écran de /boutique : « Sur quelle boutique souhaitez-vous naviguer ? »."""
    cfg = await eco.config()
    accessibles = eco._boutiques_accessibles(cfg, membre)
    if not accessibles:
        return {"flags": V2 | PRIVE, "components": [{"type": 17, "accent_color": ROUGE, "components": [_texte("Aucune boutique ne vous est accessible.")]}]}
    if len(accessibles) == 1:
        return await vue_boutique(eco, membre, accessibles[0]["id"])
    options = []
    for b in accessibles[:25]:
        o = {"label": b["nom"][:100], "value": b["id"]}
        if b.get("emoji"):
            o["emoji"] = {"name": b["emoji"]}
        if _acces_txt(b):
            o["description"] = _acces_txt(b)[:100]
        options.append(o)
    return {"flags": V2 | PRIVE, "components": [
        {"type": 17, "accent_color": OR_DEFAUT, "components": [_texte("**Sur quelle boutique souhaitez-vous naviguer ?**")]},
        {"type": 1, "components": [{"type": 3, "custom_id": "eco:shop", "placeholder": "Sélectionnez une boutique…", "options": options}]},
    ]}


def _trier(articles: list[dict], tri: str) -> list[dict]:
    cles = {"prix_asc": lambda a: (a.get("prix") or 0), "prix_desc": lambda a: -(a.get("prix") or 0),
            "nom": lambda a: (a.get("nom") or "").lower(), "ordre": lambda a: (a.get("ordre") or 0, a.get("prix") or 0)}
    return sorted(articles, key=cles.get(tri, cles["ordre"]))


async def vue_boutique(eco, membre, boutique_id: str, page: int = 0, tri: str | None = None) -> dict:
    cfg = await eco.config()
    accessibles = eco._boutiques_accessibles(cfg, membre)
    b = next((x for x in accessibles if x["id"] == boutique_id), None)
    if not b:
        return {"flags": V2 | PRIVE, "components": [{"type": 17, "accent_color": ROUGE, "components": [_texte("Cette boutique ne vous est pas accessible.")]}]}
    tri = tri or (b.get("tri") if b.get("tri") in dict(TRIS) else "ordre")
    articles = _trier([a for a in cfg["articles"] if a.get("boutique") == b["id"]], tri)
    pages = max(1, -(-len(articles) // PAR_PAGE_BOUTIQUE))
    page = max(0, min(page, pages - 1))
    joueur = await eco.joueur_de(membre)
    emoji_or = {"id": or_id(cfg), "name": "or"} if or_id(cfg) else None

    blocs = [_texte(f'## {b.get("emoji") or "🛒"} {b["nom"]}'.strip() + (f'\n{b["description"]}' if b.get("description") else ""))]
    if b.get("banniere", "").startswith("http"):
        blocs.append({"type": 12, "items": [{"media": {"url": b["banniere"]}}]})
    blocs.append({"type": 14, "divider": True, "spacing": 1})
    for a in articles[page * PAR_PAGE_BOUTIQUE:(page + 1) * PAR_PAGE_BOUTIQUE]:
        epuise = not a.get("stock_illimite") and (a.get("stock") or 0) <= 0
        lignes = [f'### {a["nom"]} {a.get("emoji") or ""}'.rstrip()]
        if not a.get("stock_illimite"):
            lignes.append("-# ❌ Quantité épuisée" if epuise else f"-# Quantité restante : {a.get('stock')}")
        if a.get("type") == "role_temporaire" and a.get("duree_jours"):
            lignes.append(f"-# ⏳ Durée : {a['duree_jours']:g} jour{'s' if a['duree_jours'] > 1 else ''}")
        if a.get("description"):
            lignes.append(a["description"])
        blocs.append({"type": 9, "components": [_texte("\n".join(lignes)[:1500])],
                      "accessory": _bouton(f'{_n(a.get("prix"))} - Acheter', f'eco:buy:{b["id"]}:{a["id"]}', style=1, disabled=epuise, emoji=emoji_or)})
    if not articles:
        blocs.append(_texte("*Aucun article pour le moment.*"))
    blocs.append({"type": 14, "divider": True, "spacing": 1})
    solde = f"Vous avez {abrege((joueur or {}).get('eco_or'))} {or_txt(cfg)} • " if joueur else ""
    blocs.append(_texte(f"-# {solde}Tri : {dict(TRIS).get(tri, tri)}"))
    blocs.append({"type": 1, "components": [
        _bouton("Précédent", f'eco:pg:{b["id"]}:{page - 1}:{tri}', disabled=page == 0),
        _bouton(f"Page {page + 1}/{pages}", "eco:rien", disabled=True),
        _bouton("Suivant", f'eco:pg:{b["id"]}:{page + 1}:{tri}', style=1, disabled=page >= pages - 1),
        _bouton("Fermer", "eco:fermer"),
    ]})
    blocs.append({"type": 1, "components": [{"type": 3, "custom_id": f'eco:tri:{b["id"]}', "placeholder": "Trier par…",
                                              "options": [{"label": t, "value": k, "default": k == tri} for k, t in TRIS]}]})
    composants = [{"type": 17, "accent_color": _couleur(b.get("couleur")), "components": blocs}]
    autres = [x for x in accessibles if x["id"] != b["id"]]
    if autres:
        composants.append({"type": 1, "components": [{"type": 3, "custom_id": "eco:shop", "placeholder": "Sélectionnez une autre boutique…",
                                                      "options": [{"label": x["nom"][:100], "value": x["id"], **({"emoji": {"name": x["emoji"]}} if x.get("emoji") else {}),
                                                                   **({"description": _acces_txt(x)[:100]} if _acces_txt(x) else {})} for x in autres[:25]]}]})
    return {"flags": V2 | PRIVE, "components": composants}


def boutique_fermee() -> dict:
    return {"flags": V2 | PRIVE, "components": [{"type": 17, "accent_color": GRIS, "components": [_texte("Boutique fermée.")]}]}


async def fenetre_quantite(eco, membre, boutique_id: str, article_id: str) -> dict | None:
    """Fenêtre (modal) « Acheter un article » : quantité souhaitée, coût unitaire et solde rappelés."""
    cfg = await eco.config()
    a = next((x for x in cfg["articles"] if x["id"] == article_id), None)
    if not a:
        return None
    joueur = await eco.joueur_de(membre)
    maxi = 25 if a.get("stock_illimite") else max(1, min(25, a.get("stock") or 1))
    infos = f'Coût unitaire : {_n(a.get("prix"))} Or • Votre solde : {abrege((joueur or {}).get("eco_or"))} Or'
    return {"title": "Acheter un article", "custom_id": f"eco:buyq:{boutique_id}:{article_id}", "components": [
        {"type": 1, "components": [{"type": 4, "custom_id": "quantite", "style": 1, "label": f"Quantité souhaitée (entre 1 et {maxi})",
                                     "placeholder": infos[:100], "value": "1", "min_length": 1, "max_length": 2, "required": True}]},
    ]}


# ---------------------------------------------------------------- classements

async def vue_classement(eco, genre: str, page: int = 0) -> tuple[dict, list]:
    """genre « argent » (Or) ou « niveau » (XP), 5 par page comme Draftbot."""
    from app.economie import niveau_de
    cfg = await eco.config()
    champ = "eco_or" if genre == "argent" else "eco_xp"
    joueurs = [j for j in await eco.pb.lister("joueurs", tri=f"-{champ}") if (j.get(champ) or 0) > 0]
    pages = max(1, -(-len(joueurs) // PAR_PAGE_TOP))
    page = max(0, min(page, pages - 1))
    medailles = ["🥇", "🥈", "🥉"]
    lignes = []
    for i, j in enumerate(joueurs[page * PAR_PAGE_TOP:(page + 1) * PAR_PAGE_TOP], page * PAR_PAGE_TOP):
        tete = f'{medailles[i] if i < 3 else "🏅"} **{j.get("pseudo")}**' + (f' (`@{j["pseudo_discord"]}`)' if j.get("pseudo_discord") else "")
        if genre == "argent":
            lignes.append(f'{tete}\n➥ {_n(j.get("eco_or"))} {or_txt(cfg)}')
        else:
            lignes.append(f'{tete}\n➥ Niveau {niveau_de(cfg["courbe"], j.get("eco_xp") or 0)[0]} ({_n(j.get("eco_xp"))} xp)')
    titre = "Classement d'économie de Chroniques du Temps" if genre == "argent" else "Classement de niveaux de : Chroniques du Temps"
    embed = {"title": titre, "description": "\n".join(lignes) or "—", "color": 0xD6C410 if genre == "argent" else 0xCC2020}
    boutons = [
        _bouton("Précédent", f"eco:top:{genre}:{page - 1}", style=1, disabled=page == 0),
        _bouton(f"Page {page + 1}/{pages}", "eco:rien", disabled=True),
        _bouton("Suivant", f"eco:top:{genre}:{page + 1}", style=1, disabled=page >= pages - 1),
    ]
    lien = (cfg["reglages"].get("lien_classement") or "").strip()
    if lien.startswith("http"):
        boutons.append({"type": 2, "style": 5, "label": "Voir l'ensemble du classement", "url": lien})
    return embed, [{"type": 1, "components": boutons}]


# ---------------------------------------------------------------- inventaire (grille)

async def vue_inventaire(eco, cible, page: int = 0) -> tuple[dict, list]:
    import asyncio
    cfg = await eco.config()
    joueur = await eco.joueur_de(cible)
    if not joueur:
        return erreur("Aucune fiche joueur liée à ce compte Discord."), []
    lignes, soldes, domaines = await asyncio.gather(
        eco.pb.lister("eco_inventaire", f'joueur="{echapper(joueur["id"])}" && quantite>0', expand="objet"),
        eco.pb.lister("ros_soldes", f'joueur="{echapper(joueur["id"])}"'),
        eco.pb.lister("ros_domaines", tri="ordre"),
    )
    cases = [{"name": f'{(l["expand"]["objet"].get("emoji") or "")} {l["expand"]["objet"]["nom"]}'.strip()[:256], "value": f'x {_n(l["quantite"])}', "inline": True}
             for l in sorted([l for l in lignes if (l.get("expand") or {}).get("objet")], key=lambda l: l["expand"]["objet"].get("ordre") or 0)]
    for d in domaines:
        q = sum((s.get("monnaie") or 0) for s in soldes if s.get("domaine") == d["id"])
        if q:
            cases.append({"name": f'{d.get("monnaie_emoji") or ""} {d.get("monnaie_nom")}'.strip(), "value": f"x {_n(q)}", "inline": True})
    pages = max(1, -(-len(cases) // PAR_PAGE_INV))
    page = max(0, min(page, pages - 1))
    nom = getattr(cible, "display_name", None) or joueur.get("pseudo")
    embed = {
        "author": {"name": f"Inventaire de {nom}", **({"icon_url": cible.display_avatar.url} if getattr(cible, "display_avatar", None) else {})},
        "description": f"Monnaie de {getattr(cible, 'mention', nom)} : {_n(joueur.get('eco_or'))} {or_txt(cfg)}\n━━━━━━━━━━━━━━━━━━",
        "fields": cases[page * PAR_PAGE_INV:(page + 1) * PAR_PAGE_INV] or [{"name": "Inventaire vide", "value": "—", "inline": False}],
        "color": OR_DEFAUT, "footer": {"text": "Chroniques du Temps" + (f" • page {page + 1}/{pages}" if pages > 1 else "")},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    composants = []
    if pages > 1:
        composants = [{"type": 1, "components": [
            _bouton("Précédent", f'eco:inv:{getattr(cible, "id", 0)}:{page - 1}', style=1, disabled=page == 0),
            _bouton(f"Page {page + 1}/{pages}", "eco:rien", disabled=True),
            _bouton("Suivant", f'eco:inv:{getattr(cible, "id", 0)}:{page + 1}', style=1, disabled=page >= pages - 1),
        ]}]
    return embed, composants


# ---------------------------------------------------------------- cartes image

async def carte_argent(eco, cible) -> tuple[dict | None, bytes | None]:
    from app.eco_cartes import carte
    cfg = await eco.config()
    joueur = await eco.joueur_de(cible)
    if not joueur:
        return erreur("Aucune fiche joueur liée à ce compte Discord."), None
    classement = await eco.pb.lister("joueurs", tri="-eco_or")
    place = next((i for i, j in enumerate(classement, 1) if j["id"] == joueur["id"]), 0)
    solde, record = joueur.get("eco_or") or 0, max(joueur.get("eco_or_record") or 0, joueur.get("eco_or") or 0)
    image = await carte(getattr(cible, "display_name", joueur.get("pseudo")), getattr(getattr(cible, "display_avatar", None), "url", None),
                        (214, 196, 16), solde / record if record else 0, f"{_n(solde)} Or", f"Place : {place}", f"Record : {abrege(record)}")
    return {"color": 0xD6C410, "image": {"url": "attachment://carte.png"}}, image


async def carte_niveau(eco, cible) -> tuple[dict | None, bytes | None]:
    from app.eco_cartes import carte
    from app.economie import niveau_de
    cfg = await eco.config()
    joueur = await eco.joueur_de(cible)
    if not joueur:
        return erreur("Aucune fiche joueur liée à ce compte Discord."), None
    n, fait, besoin = niveau_de(cfg["courbe"], joueur.get("eco_xp") or 0)
    classement = await eco.pb.lister("joueurs", tri="-eco_xp")
    place = next((i for i, j in enumerate(classement, 1) if j["id"] == joueur["id"]), 0)
    image = await carte(getattr(cible, "display_name", joueur.get("pseudo")), getattr(getattr(cible, "display_avatar", None), "url", None),
                        (204, 32, 32), fait / besoin if besoin else 1, f"{abrege(fait)} / {abrege(besoin)}" if besoin else "max",
                        f"Place : {place}", f"Niveau {n}")
    return {"color": 0xCC2020, "image": {"url": "attachment://carte.png"}}, image


# ---------------------------------------------------------------- composants pour les commandes à préfixe (discord.py)

def vue_discord(composants: list) -> discord.ui.View | None:
    """Convertit des boutons/menus JSON (rangées classiques) en View discord.py ; les clics arrivent par /interactions."""
    if not composants:
        return None
    vue = discord.ui.View(timeout=None)
    for ligne, rangee in enumerate(composants):
        for c in rangee.get("components", []):
            if c["type"] == 2 and c.get("style") == 5:
                vue.add_item(discord.ui.Button(label=c.get("label"), url=c["url"], row=ligne))
            elif c["type"] == 2:
                vue.add_item(discord.ui.Button(custom_id=c.get("custom_id"), label=c.get("label"), style=discord.ButtonStyle(c.get("style", 2)),
                                               disabled=c.get("disabled", False), row=ligne))
    return vue
