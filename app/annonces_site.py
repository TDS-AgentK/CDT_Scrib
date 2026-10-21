"""Messages texte demandés par le site (collection `discord_annonces`) : le bot les poste dans le salon indiqué.

Premier usage : la réclamation du gain de fiche (/admin/personnages/<id>) annonce « Nouvelle fiche sur le site
[Nom](<lien>). » dans le salon réglé sur le site (eco_reglages.salon_annonces_fiches, #général par défaut).
Comme `ros_envois` et `jets`, le site dépose une demande `statut="en_attente"` ; le bot relit la file toutes les
ANNONCES_S secondes, poste le texte (aucune mention ne notifie personne), puis note `statut` (envoye/erreur),
`envoye_le`, `lien` ou `erreur`. Une demande trop ancienne (bot arrêté longtemps) n'est plus postée, seulement marquée.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import discord

from app.pocketbase import PocketBase

log = logging.getLogger(__name__)

ANNONCES_S = 10
FRAICHEUR = timedelta(hours=24)

# (salon_id, texte) -> lien du message posté
Envoyer = Callable[[str, str], Awaitable[str]]


def _pb_date(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S.000Z")


def _age(annonce: dict, maintenant: datetime) -> timedelta:
    brut = annonce.get("created") or ""
    try:
        return maintenant - datetime.fromisoformat(brut.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return timedelta(0)


async def traiter(envoyer: Envoyer, pb: PocketBase, annonce: dict, maintenant: datetime | None = None) -> dict:
    """Poste une annonce en attente et enregistre le résultat ; renvoie la mise à jour faite."""
    maintenant = maintenant or datetime.now(timezone.utc)
    if _age(annonce, maintenant) > FRAICHEUR:
        maj = {"statut": "erreur", "erreur": "trop ancienne, non postée"}
    else:
        try:
            lien = await envoyer(str(annonce.get("salon_id") or ""), str(annonce.get("texte") or "")[:2000])
            maj = {"statut": "envoye", "envoye_le": _pb_date(maintenant), "lien": lien or "", "erreur": ""}
        except Exception as e:  # noqa: BLE001 — l'erreur est notée sur la demande, visible dans la base
            log.exception("Annonce %s non postée (salon %s)", annonce.get("id"), annonce.get("salon_id"))
            maj = {"statut": "erreur", "erreur": f"{type(e).__name__} : {e}"[:300]}
    await pb.maj("discord_annonces", annonce["id"], maj)
    return maj


async def tour(envoyer: Envoyer, pb: PocketBase) -> int:
    annonces = await pb.lister("discord_annonces", 'statut="en_attente"', tri="created")
    for annonce in annonces:
        await traiter(envoyer, pb, annonce)
    return len(annonces)


def envoyeur(client: discord.Client) -> Envoyer:
    async def envoyer(salon_id: str, texte: str) -> str:
        if not salon_id.isdigit():
            raise ValueError("salon non réglé")
        salon = client.get_channel(int(salon_id)) or await client.fetch_channel(int(salon_id))
        message = await salon.send(texte, allowed_mentions=discord.AllowedMentions.none())
        return getattr(message, "jump_url", "") or ""
    return envoyer


async def boucle(client: discord.Client, pb: PocketBase):
    await client.wait_until_ready()
    envoyer = envoyeur(client)
    while not client.is_closed():
        try:
            await tour(envoyer, pb)
        except Exception:
            log.exception("Erreur dans la boucle des annonces du site")
        await asyncio.sleep(ANNONCES_S)
