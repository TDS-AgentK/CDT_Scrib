"""Affichage de l'économie en embeds (dictionnaires JSON de l'API Discord) et composants interactifs.

Utilisé à l'identique par les commandes slash (/profil, /inventaire, /boutique, /classement) et par les
commandes à préfixe (!!niveau…). Les menus et boutons portent des custom_id « eco:… » traités dans
app/eco_interactions.py (ils arrivent par l'endpoint /interactions).
"""
import discord

from app.pocketbase import echapper

OR_DEFAUT = 0xC5A24F
VERT, ROUGE = 0x3BA55C, 0xC0392B


def _n(v) -> str:
    return f"{int(v or 0):,}".replace(",", " ")


def _couleur(hexa: str | None) -> int:
    try:
        return int(hexa.lstrip("#"), 16) if hexa and hexa.startswith("#") else OR_DEFAUT
    except ValueError:
        return OR_DEFAUT


def _barre(fait: int, besoin: int, cases: int = 12) -> str:
    if not besoin:
        return "▰" * cases
    plein = max(0, min(cases, round(cases * fait / besoin)))
    return "▰" * plein + "▱" * (cases - plein)


def or_txt(cfg: dict) -> str:
    """Emoji d'Or du serveur (ID enregistré dans les réglages), sinon le mot « Or »."""
    eid = (cfg["reglages"].get("or_emoji_id") or "").strip()
    return f"<:or:{eid}>" if eid.isdigit() else "Or"


def xp_txt(cfg: dict) -> str:
    return f'{cfg["reglages"].get("xp_emoji") or ""} {cfg["reglages"].get("xp_nom") or "XP"}'.strip()


def _auteur(membre) -> dict:
    a = {"name": getattr(membre, "display_name", None) or str(membre)}
    avatar = getattr(membre, "display_avatar", None)
    if avatar:
        a["icon_url"] = avatar.url
    return a


def erreur(texte: str) -> dict:
    return {"description": texte, "color": ROUGE}


# ---------------------------------------------------------------- profil / niveau

async def vue_profil(eco, cible) -> dict:
    from app.economie import niveau_de
    cfg = await eco.config()
    joueur = await eco.joueur_de(cible)
    if not joueur:
        return erreur("Aucune fiche joueur liée à ce compte Discord.")
    xp = joueur.get("eco_xp") or 0
    n, fait, besoin = niveau_de(cfg["courbe"], xp)
    classement = await eco.pb.lister("joueurs", tri="-eco_xp")
    rang = next((i for i, j in enumerate(classement, 1) if j["id"] == joueur["id"]), None)
    pct = round(100 * fait / besoin) if besoin else 100
    embed = {
        "author": _auteur(cible), "title": f"Niveau {n}", "color": OR_DEFAUT,
        "description": f"{_barre(fait, besoin)}  **{pct} %**\n"
                       + (f"{_n(fait)} / {_n(besoin)} XP vers le niveau {n + 1}" if besoin else "Niveau maximum atteint"),
        "fields": [
            {"name": xp_txt(cfg), "value": f"**{_n(xp)}**", "inline": True},
            {"name": "Or", "value": f"**{_n(joueur.get('eco_or'))}** {or_txt(cfg)}", "inline": True},
            {"name": "Classement", "value": f"**#{rang}**" if rang else "—", "inline": True},
            {"name": "Or dépensé", "value": f"{_n(joueur.get('eco_or_depense'))} {or_txt(cfg)}", "inline": True},
        ],
    }
    avatar = getattr(cible, "display_avatar", None)
    if avatar:
        embed["thumbnail"] = {"url": avatar.url}
    return embed


# ---------------------------------------------------------------- inventaire

async def vue_inventaire(eco, cible) -> list[dict]:
    """Un ou plusieurs embeds (les longs inventaires sont répartis sur plusieurs champs)."""
    import asyncio
    cfg = await eco.config()
    joueur = await eco.joueur_de(cible)
    if not joueur:
        return [erreur("Aucune fiche joueur liée à ce compte Discord.")]
    lignes, soldes, domaines = await asyncio.gather(
        eco.pb.lister("eco_inventaire", f'joueur="{echapper(joueur["id"])}" && quantite>0', expand="objet"),
        eco.pb.lister("ros_soldes", f'joueur="{echapper(joueur["id"])}"'),
        eco.pb.lister("ros_domaines", tri="ordre"),
    )
    champs = []
    for d in domaines:
        q = sum((s.get("monnaie") or 0) for s in soldes if s.get("domaine") == d["id"])
        if q:
            champs.append({"name": f'{d.get("monnaie_emoji") or ""} {d.get("monnaie_nom")}'.strip(), "value": f"**{_n(q)}**", "inline": True})
    objets = sorted([l for l in lignes if (l.get("expand") or {}).get("objet")], key=lambda l: l["expand"]["objet"].get("ordre") or 0)
    bloc, titre = [], f"🎒 Objets ({sum(l['quantite'] for l in objets)})"
    for l in objets:
        o = l["expand"]["objet"]
        ligne = f'`×{l["quantite"]}` {o.get("emoji") or ""} {o["nom"]}'.replace("  ", " ")
        if sum(len(x) + 1 for x in bloc) + len(ligne) > 1000:
            champs.append({"name": titre, "value": "\n".join(bloc), "inline": False})
            bloc, titre = [], "🎒 Objets (suite)"
        bloc.append(ligne)
    if bloc:
        champs.append({"name": titre, "value": "\n".join(bloc), "inline": False})
    embed = {"author": _auteur(cible), "title": "Inventaire", "color": OR_DEFAUT,
             "description": f"**{_n(joueur.get('eco_or'))}** {or_txt(cfg)}", "fields": champs[:25]}
    if not objets and len(champs) == 0:
        embed["description"] += "\n\n*Inventaire vide.*"
    return [embed]


# ---------------------------------------------------------------- classement

async def vue_classement(eco) -> dict:
    from app.economie import niveau_de
    cfg = await eco.config()
    joueurs = [j for j in await eco.pb.lister("joueurs", tri="-eco_xp") if (j.get("eco_xp") or 0) > 0]
    medailles = ["🥇", "🥈", "🥉"]
    lignes = []
    for i, j in enumerate(joueurs[:10]):
        rang = medailles[i] if i < 3 else f"`#{i + 1}`"
        n = niveau_de(cfg["courbe"], j.get("eco_xp") or 0)[0]
        lignes.append(f'{rang} **{j.get("pseudo")}** · niveau **{n}** · {_n(j.get("eco_xp"))} XP · {_n(j.get("eco_or"))} {or_txt(cfg)}')
    return {"title": f"🏆 Classement — {xp_txt(cfg)}", "description": "\n".join(lignes) or "—", "color": OR_DEFAUT}


# ---------------------------------------------------------------- boutique

def _detail_article(cfg: dict, a: dict) -> str:
    morceaux = [f"**{_n(a.get('prix'))}** {or_txt(cfg)}"]
    if a.get("type") == "role_temporaire" and a.get("duree_jours"):
        morceaux.append(f"⏳ {a['duree_jours']:g} j")
    morceaux.append("∞" if a.get("stock_illimite") else (f"Stock : {a.get('stock') or 0}" if (a.get("stock") or 0) > 0 else "**Épuisé**"))
    texte = " · ".join(morceaux)
    if a.get("description"):
        texte += "\n" + a["description"][:150]
    return texte


async def vue_boutique(eco, membre, boutique_id: str | None = None) -> tuple[dict, list]:
    cfg = await eco.config()
    accessibles = eco._boutiques_accessibles(cfg, membre)
    if not accessibles:
        return erreur("Aucune boutique ne t'est accessible."), []
    b = next((x for x in accessibles if x["id"] == boutique_id), accessibles[0])
    articles = eco._articles(cfg, b)
    embed = {
        "title": f'{b.get("emoji") or "🛒"} {b["nom"]}'.strip(), "color": _couleur(b.get("couleur")),
        "description": b.get("description") or None,
        "fields": [{"name": f'{a.get("emoji") or ""} {a["nom"]}'.strip()[:256], "value": _detail_article(cfg, a), "inline": True}
                   for a in articles[:24]],
        "footer": {"text": "Choisis un article dans le menu pour l'acheter."},
    }
    if b.get("banniere", "").startswith("http"):
        embed["image"] = {"url": b["banniere"]}
    composants = []
    if len(accessibles) > 1:
        composants.append({"type": 1, "components": [{
            "type": 3, "custom_id": "eco:bq", "placeholder": "Changer de boutique",
            "options": [{"label": x["nom"][:100], "value": x["id"], "default": x["id"] == b["id"]} for x in accessibles[:25]],
        }]})
    achetables = [a for a in articles if a.get("stock_illimite") or (a.get("stock") or 0) > 0][:25]
    if achetables:
        composants.append({"type": 1, "components": [{
            "type": 3, "custom_id": f"eco:art:{b['id']}", "placeholder": "🛒 Acheter un article…",
            "options": [{"label": a["nom"][:100], "value": a["id"],
                         "description": f"{_n(a.get('prix'))} Or" + ("" if a.get("stock_illimite") else f" · stock {a.get('stock')}")}
                        for a in achetables],
        }]})
    return embed, composants


async def vue_confirmation(eco, membre, boutique_id: str, article_id: str) -> tuple[dict, list]:
    cfg = await eco.config()
    article = next((a for a in cfg["articles"] if a["id"] == article_id), None)
    if not article or not any(b["id"] == boutique_id for b in eco._boutiques_accessibles(cfg, membre)):
        return erreur("Cet article n'est plus disponible pour toi."), []
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return erreur("Aucune fiche joueur liée à ce compte Discord."), []
    solde, prix = joueur.get("eco_or") or 0, article.get("prix") or 0
    assez = solde >= prix
    embed = {
        "title": "Confirmer l'achat", "color": OR_DEFAUT if assez else ROUGE,
        "description": f'{article.get("emoji") or ""} **{article["nom"]}**\n{_detail_article(cfg, article)}\n\n'
                       + (f"Ton solde : {_n(solde)} → **{_n(solde - prix)}** {or_txt(cfg)}" if assez
                          else f"Il te manque **{_n(prix - solde)}** {or_txt(cfg)}."),
    }
    boutons = [
        {"type": 2, "style": 3, "label": "Acheter", "emoji": {"name": "🛒"}, "custom_id": f"eco:buy:{boutique_id}:{article_id}", "disabled": not assez},
        {"type": 2, "style": 2, "label": "Annuler", "custom_id": "eco:no"},
    ]
    return embed, [{"type": 1, "components": boutons}]


def resultat_achat(ok: bool, texte: str) -> dict:
    return {"title": "Achat effectué ✅" if ok else "Achat impossible", "description": texte, "color": VERT if ok else ROUGE}


# ---------------------------------------------------------------- composants pour les commandes à préfixe (discord.py)

def vue_discord(composants: list) -> discord.ui.View | None:
    """Convertit les composants JSON en View discord.py (les clics arrivent quand même par /interactions)."""
    if not composants:
        return None
    vue = discord.ui.View(timeout=None)
    for ligne, rangee in enumerate(composants):
        for c in rangee["components"]:
            if c["type"] == 3:
                vue.add_item(discord.ui.Select(custom_id=c["custom_id"], placeholder=c.get("placeholder"), row=ligne, options=[
                    discord.SelectOption(label=o["label"], value=o["value"], description=o.get("description"), default=o.get("default", False))
                    for o in c["options"]]))
            elif c["type"] == 2:
                vue.add_item(discord.ui.Button(custom_id=c.get("custom_id"), label=c.get("label"), style=discord.ButtonStyle(c.get("style", 2)),
                                               disabled=c.get("disabled", False), row=ligne))
    return vue
