"""Sessions de jeu (collection `sessions_jeu` de la base du site) : commande /session, utilisable par tout le monde.

- /session ouvrir [mj] [event] : ouvre une session dirigée par le MJ choisi (vous par défaut) ; refusé si une session
  est déjà ouverte. Le bandeau « Session en cours, dirigée par … » apparaît alors sur les fiches de jeu du site.
- /session rejoindre perso : ajoute à la table une fiche de jeu de vos personnages (autocomplétion).
- /session voir : MJ, table, event, durée et nombre de jets de la session ouverte.
- /session clore : par le MJ, la personne qui l'a ouverte ou un administrateur du serveur.

Le MJ est un compte du site (`users`), retrouvé par le membre Discord : joueurs.discord_id → users.joueur.
"""
from datetime import datetime, timezone

import discord

from app.pocketbase import echapper

OR_CDT = 0xC9A24C


def _maintenant() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")


def _date(brut: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(brut).replace("Z", "+00:00").replace(" ", "T")) if brut else None
    except ValueError:
        return None


async def compte_de(eco, membre: discord.abc.User) -> tuple[dict | None, dict | None]:
    """(joueur, compte du site) d'un membre Discord ; (None, None) s'il n'est relié à aucun joueur."""
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return None, None
    user = await eco.pb.premier("users", f'joueur="{echapper(joueur["id"])}"')
    return joueur, user


async def session_ouverte(eco) -> dict | None:
    sessions = await eco.pb.lister("sessions_jeu", 'statut="ouverte"', tri="-ouverture", expand="mj,table,table.personnage")
    return sessions[0] if sessions else None


def _table(session: dict) -> list[str]:
    fiches = (session.get("expand") or {}).get("table") or []
    return [((f.get("expand") or {}).get("personnage") or {}).get("prenom") or f.get("nom") or "?" for f in fiches]


def _embed(titre: str, session: dict, extra: str = "") -> dict:
    mj = ((session.get("expand") or {}).get("mj") or {}).get("name") or "?"
    lignes = [f"Dirigée par **{mj}**"]
    if session.get("event"):
        lignes.append(f"Event : {session['event']}")
    table = _table(session)
    lignes.append(f"Table : {', '.join(table)}" if table else "Table : personne pour l'instant · `/session rejoindre`")
    if extra:
        lignes.append(extra)
    return {"title": titre, "description": "\n".join(lignes), "color": OR_CDT, "footer": {"text": "Chroniques du Temps · fiches de jeu"}}


async def ouvrir(eco, membre: discord.Member, mj: discord.Member | None, event: str | None) -> tuple[bool, dict]:
    deja = await session_ouverte(eco)
    if deja:
        return False, _embed("🎲 Une session est déjà en cours", deja, "Elle doit être close (`/session clore`) avant d'en ouvrir une autre.")
    _, ouvreur = await compte_de(eco, membre)
    cible = mj or membre
    _, compte_mj = await compte_de(eco, cible)
    if not compte_mj:
        return False, {"description": f"{cible.mention} n'est relié à aucun compte joueur du site : il ne peut pas être MJ.", "color": 0xB0473B}
    s = await eco.pb.creer("sessions_jeu", {
        "mj": compte_mj["id"], "ouverte_par": (ouvreur or {}).get("id", ""), "statut": "ouverte", "table": [],
        "event": (event or "").strip()[:200], "ouverture": _maintenant(),
    })
    s = await eco.pb.requete("GET", f'/api/collections/sessions_jeu/records/{s["id"]}', params={"expand": "mj,table,table.personnage"})
    return True, _embed("🎲 Session ouverte", s, f"Ouverte par {membre.mention}")


async def rejoindre(eco, membre: discord.Member, fiche_id: str | None) -> tuple[bool, dict]:
    s = await session_ouverte(eco)
    if not s:
        return False, {"description": "Aucune session ouverte : `/session ouvrir` d'abord.", "color": 0xB0473B}
    joueur, _ = await compte_de(eco, membre)
    fiches = await mes_fiches(eco, joueur)
    fiche = next((f for f in fiches if f["id"] == fiche_id), None)
    if not fiche:
        return False, {"description": "Choisissez une fiche de jeu de l'un de vos personnages dans la liste.", "color": 0xB0473B}
    if fiche["id"] in (s.get("table") or []):
        return False, _embed("🎲 Déjà à la table", s)
    s = await eco.pb.requete("PATCH", f'/api/collections/sessions_jeu/records/{s["id"]}',
                             json={"table": [*(s.get("table") or []), fiche["id"]]}, params={"expand": "mj,table,table.personnage"})
    nom = ((fiche.get("expand") or {}).get("personnage") or {}).get("prenom") or "?"
    return True, _embed(f"🎲 {nom} rejoint la table", s)


async def voir(eco) -> dict:
    s = await session_ouverte(eco)
    if not s:
        return {"description": "Aucune session en cours. `/session ouvrir` pour en commencer une.", "color": OR_CDT}
    debut = _date(s.get("ouverture"))
    duree = ""
    if debut:
        minutes = int((datetime.now(timezone.utc) - debut).total_seconds() // 60)
        duree = f"Depuis {minutes // 60} h {minutes % 60:02d}" if minutes >= 60 else f"Depuis {minutes} min"
    jets = await eco.pb.lister("jets", f'session="{echapper(s["id"])}"')
    return _embed("🎲 Session en cours", s, " · ".join(x for x in (duree, f"{len(jets)} jet(s)") if x))


async def clore(eco, membre: discord.Member) -> tuple[bool, dict]:
    s = await session_ouverte(eco)
    if not s:
        return False, {"description": "Aucune session ouverte.", "color": 0xB0473B}
    _, compte = await compte_de(eco, membre)
    autorise = (compte and compte["id"] in (s.get("mj"), s.get("ouverte_par"))) or membre.guild_permissions.manage_guild
    if not autorise:
        mj = ((s.get("expand") or {}).get("mj") or {}).get("name") or "le MJ"
        return False, {"description": f"Seul {mj}, la personne qui a ouvert la session ou un administrateur peut la clore.", "color": 0xB0473B}
    await eco.pb.maj("sessions_jeu", s["id"], {"statut": "close", "cloture": _maintenant()})
    jets = await eco.pb.lister("jets", f'session="{echapper(s["id"])}"')
    return True, _embed("🎲 Session close", s, f"Close par {membre.mention} · {len(jets)} jet(s) pendant la session")


async def mes_fiches(eco, joueur: dict | None) -> list[dict]:
    """Fiches de jeu des personnages du joueur (le personnage du site porte le pseudo du joueur)."""
    if not joueur:
        return []
    return await eco.pb.lister("fiches_jeu", f'personnage.joueur="{echapper(joueur.get("pseudo") or "")}"', expand="personnage")
