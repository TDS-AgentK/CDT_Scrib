"""Rôles Discord gagnés sur le site (collection `discord_roles_a_donner`) : le bot les donne.

Premier usage : la récompense d'un record de quiz (/quiz, réglée par le super admin dans /gestion/quiz), qui peut
combiner Or, XP, objets (crédités par le site) et un rôle définitif ou temporaire (donné ici).
Comme `discord_annonces`, le site dépose une demande `statut="en_attente"` (joueur, discord_id, role_id, role_nom,
role_heures, motif) ; le bot relit la file toutes les ROLES_S secondes et donne le rôle avec le même code que les
loteries (loteries._donner_role → Economie.donner_role : vérifie que le rôle est donnable, note un rôle temporaire
dans eco_roles_temporaires, retiré à l'échéance). Il note ensuite `statut` (donne/erreur), `donne_le` ou `erreur`.
"""

import asyncio
import logging
from datetime import datetime, timezone

from app import loteries
from app.pocketbase import echapper

log = logging.getLogger(__name__)

ROLES_S = 15


def _pb_date(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S.000Z")


async def traiter(eco, guild, demande: dict, maintenant: datetime | None = None) -> dict:
    """Donne le rôle d'une demande en attente et enregistre le résultat ; renvoie la mise à jour faite."""
    maintenant = maintenant or datetime.now(timezone.utc)
    j = None
    if demande.get("joueur"):
        j = await eco.pb.premier("joueurs", f'id="{echapper(demande["joueur"])}"')
    j = dict(j or {"id": demande.get("joueur") or ""})
    j["discord_id"] = j.get("discord_id") or demande.get("discord_id") or ""
    raison = await loteries._donner_role(eco, guild, j, demande, origine=demande.get("motif") or "site")
    maj = ({"statut": "erreur", "erreur": str(raison)[:300]} if raison
           else {"statut": "donne", "donne_le": _pb_date(maintenant), "erreur": ""})
    if raison:
        log.warning("Rôle %s non donné à %s : %s", demande.get("role_nom") or demande.get("role_id"), j.get("id"), raison)
    await eco.pb.maj("discord_roles_a_donner", demande["id"], maj)
    return maj


async def tour(eco) -> int:
    demandes = await eco.pb.lister("discord_roles_a_donner", 'statut="en_attente"', tri="created")
    if not demandes:
        return 0
    guild = eco.client.get_guild(eco.guild_id) if eco.guild_id else (eco.client.guilds[0] if eco.client.guilds else None)
    for demande in demandes:
        await traiter(eco, guild, demande)
    return len(demandes)


async def boucle(eco):
    await eco.client.wait_until_ready()
    while not eco.client.is_closed():
        try:
            await tour(eco)
        except Exception:
            log.exception("Erreur dans la boucle des rôles du site")
        await asyncio.sleep(ROLES_S)
