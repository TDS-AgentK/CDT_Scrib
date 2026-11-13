"""Rôles Discord de chaque joueur copiés dans sa fiche `joueurs` (champ JSON `roles_discord`, liste de
{"id", "nom"} ; `roles_discord_maj` = date de la copie), créés par scripts/setup-roles-joueurs.mjs du site.

Pourquoi : une missive envoyée depuis la boîte aux lettres du site (lib/boiteAuxLettres.ts, lib/gainMissive.ts)
rapporte de l'Or et de l'XP comme un message posté sur Discord. Pour appliquer les rôles bonus de la page Économie
(eco_roles_bonus : bonus d'XP / d'Or, rôle « sans gain ») comme Economie.gain, le site doit connaître les rôles du
joueur : le bot les lui recopie.

Copie : à chaque changement de rôles ou arrivée sur le serveur (branchée dans journal.brancher, qui détient
on_member_update / on_member_join), puis pour tous les joueurs au démarrage et toutes les COPIE_S secondes (rattrape
un évènement manqué pendant une coupure). Un joueur absent du serveur reçoit une liste vide. Une erreur (ex. champ
pas encore créé) ne bloque jamais le bot.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

log = logging.getLogger("cdt_scrib.roles_joueurs")

COPIE_S = 3600


def _pb_date(d: datetime | None = None) -> str:
    return (d or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S.000Z")


def roles_de(membre) -> list[dict]:
    """Rôles du membre (sans @everyone), du plus haut au plus bas ; liste vide si le membre n'est pas là."""
    if membre is None:
        return []
    roles = [r for r in getattr(membre, "roles", []) if not (getattr(r, "is_default", None) and r.is_default())]
    roles.sort(key=lambda r: getattr(r, "position", 0), reverse=True)
    return [{"id": str(r.id), "nom": r.name} for r in roles]


def _memes(a, b) -> bool:
    """Mêmes rôles (id et nom), quel que soit l'ordre."""
    cles = lambda l: sorted((str(x.get("id")), str(x.get("nom"))) for x in (l if isinstance(l, list) else []) if isinstance(x, dict))
    return cles(a) == cles(b)


async def ecrire(pb, joueur: dict, roles: list[dict]) -> bool:
    """Note les rôles dans la fiche si elle ne les a pas déjà ; renvoie True si la fiche a changé."""
    if _memes(joueur.get("roles_discord"), roles):
        return False
    await pb.maj("joueurs", joueur["id"], {"roles_discord": roles, "roles_discord_maj": _pb_date()})
    return True


async def noter(eco, membre) -> bool:
    """Copie les rôles d'un membre dans sa fiche joueur (s'il en a une)."""
    try:
        joueur = await eco.joueur_de(membre)
        return bool(joueur) and await ecrire(eco.pb, joueur, roles_de(membre))
    except Exception:
        log.exception("Rôles de %s non copiés dans sa fiche joueur", getattr(membre, "name", "?"))
        return False


async def tout_copier(eco, guild) -> int:
    """Copie les rôles de tous les joueurs qui ont un ID Discord ; renvoie le nombre de fiches modifiées."""
    if guild is None:
        return 0
    modifies = 0
    for j in await eco.pb.lister("joueurs"):
        did = str(j.get("discord_id") or "")
        if not re.fullmatch(r"\d{15,21}", did):
            continue
        try:
            if await ecrire(eco.pb, j, roles_de(guild.get_member(int(did)))):
                modifies += 1
        except Exception:
            log.exception("Rôles du joueur %s non copiés", j.get("id"))
    if modifies:
        log.info("Rôles Discord copiés dans %d fiche(s) joueur", modifies)
    return modifies


async def boucle(eco):
    await eco.client.wait_until_ready()
    while not eco.client.is_closed():
        try:
            guild = eco.client.get_guild(eco.guild_id) if eco.guild_id else (eco.client.guilds[0] if eco.client.guilds else None)
            await tout_copier(eco, guild)
        except Exception:
            log.exception("Erreur dans la copie des rôles des joueurs")
        await asyncio.sleep(COPIE_S)
