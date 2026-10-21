"""Messages de /fiche voir supprimés automatiquement 24 h après leur publication.

Chaque message publié (réponse + illustrations) est noté dans la base des fiches avec son heure d'expiration ;
un balayage au démarrage puis toutes les 5 minutes supprime ceux qui ont expiré, même après un redémarrage.
La suppression passe par le jeton du bot (le jeton d'interaction ne vaut que 15 minutes).
"""
import asyncio
import logging
import time

import httpx

from app.database import Database

log = logging.getLogger("cdt_scrib")

DUREE_SECONDES = 24 * 3600
INTERVALLE_SECONDES = 5 * 60

# Codes d'erreur Discord : le message ou le salon n'existe plus, ou le bot n'y a plus accès → rien à retenter.
ERREURS_DEFINITIVES = {10003, 10008, 50001}


async def programmer(db: Database, message: dict, maintenant: float | None = None) -> None:
    """Note un message renvoyé par Discord (objet message JSON) pour suppression dans 24 h."""
    if not message or not message.get("id") or not message.get("channel_id"):
        return
    expire_at = int((maintenant if maintenant is not None else time.time()) + DUREE_SECONDES)
    await db.programmer_suppression(message["channel_id"], message["id"], expire_at)


async def supprimer_message(client: httpx.AsyncClient, token: str, channel_id: str, message_id: str) -> bool:
    """True si le message n'existe plus (supprimé maintenant ou déjà parti) ; False s'il faut retenter plus tard."""
    try:
        resp = await client.delete(
            f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}",
            headers={"Authorization": f"Bot {token}"},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        log.warning("Suppression du message %s reportée (réseau) : %s", message_id, exc)
        return False
    if resp.status_code in (200, 204):
        return True
    try:
        code = resp.json().get("code")
    except ValueError:
        code = None
    if resp.status_code in (403, 404) and code in ERREURS_DEFINITIVES:
        return True
    log.warning("Suppression du message %s reportée : %s %s", message_id, resp.status_code, resp.text[:200])
    return False


async def balayer(db: Database, supprimer, maintenant: float | None = None) -> int:
    """Supprime les messages expirés ; `supprimer(channel_id, message_id)` renvoie True si c'est réglé."""
    faits = 0
    for channel_id, message_id in await db.suppressions_dues(int(maintenant if maintenant is not None else time.time())):
        if await supprimer(channel_id, message_id):
            await db.oublier_suppression(channel_id, message_id)
            faits += 1
    return faits


async def boucle(db: Database, token: str) -> None:
    async with httpx.AsyncClient() as client:
        async def supprimer(channel_id, message_id):
            return await supprimer_message(client, token, channel_id, message_id)

        while True:
            try:
                n = await balayer(db, supprimer)
                if n:
                    log.info("/fiche voir : %s message(s) expiré(s) supprimé(s).", n)
            except Exception:
                log.exception("Balayage des messages /fiche voir en échec")
            await asyncio.sleep(INTERVALLE_SECONDES)
