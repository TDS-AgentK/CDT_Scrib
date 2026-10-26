"""Messages texte demandés par le site (collection `discord_annonces`) : le bot les poste dans le salon indiqué.

Premier usage : la réclamation du gain de fiche (/admin/personnages/<id>) annonce « Nouvelle fiche sur le site
[Nom](<lien>). » dans le salon réglé sur le site (eco_reglages.salon_annonces_fiches, #général par défaut).
Comme `ros_envois` et `jets`, le site dépose une demande `statut="en_attente"` ; le bot relit la file toutes les
ANNONCES_S secondes, poste le texte (aucune mention ne notifie personne), puis note `statut` (envoye/erreur),
`envoye_le`, `lien` ou `erreur`. Une demande trop ancienne (bot arrêté longtemps) n'est plus postée, seulement marquée.

Missives de la boîte aux lettres du site (lib/boiteAuxLettres.ts) : une longue missive arrive en plusieurs demandes
(2000 caractères au plus chacune). Si le joueur a coché « Supprimer le message au bout de trois jours », chaque demande
porte `supprimer_le` : le bot note `message_id` à l'envoi, puis efface le message une fois la date passée et marque la
demande `statut="supprime"` (message déjà effacé à la main : marqué pareil).
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
# (salon_id, message_id) -> efface le message (sans erreur s'il n'existe plus)
Supprimer = Callable[[str, str], Awaitable[None]]


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
            # Lien https://discord.com/channels/<serveur>/<salon>/<message> : l'ID sert à l'effacement programmé.
            if annonce.get("supprimer_le") and (lien or "").rstrip("/").split("/")[-1].isdigit():
                maj["message_id"] = lien.rstrip("/").split("/")[-1]
        except Exception as e:  # noqa: BLE001 — l'erreur est notée sur la demande, visible dans la base
            log.exception("Annonce %s non postée (salon %s)", annonce.get("id"), annonce.get("salon_id"))
            maj = {"statut": "erreur", "erreur": f"{type(e).__name__} : {e}"[:300]}
    await pb.maj("discord_annonces", annonce["id"], maj)
    return maj


async def tour(envoyer: Envoyer, pb: PocketBase, maintenant: datetime | None = None) -> int:
    annonces = await pb.lister("discord_annonces", 'statut="en_attente"', tri="created")
    for annonce in annonces:
        await traiter(envoyer, pb, annonce, maintenant)
    return len(annonces)


async def tour_suppressions(supprimer: Supprimer, pb: PocketBase, maintenant: datetime | None = None) -> int:
    """Efface les messages postés dont la date `supprimer_le` est passée ; renvoie le nombre traité."""
    maintenant = maintenant or datetime.now(timezone.utc)
    filtre = f'statut="envoye" && supprimer_le!="" && supprimer_le<="{_pb_date(maintenant)}"'
    annonces = await pb.lister("discord_annonces", filtre, tri="supprimer_le")
    for annonce in annonces:
        try:
            if annonce.get("message_id"):
                await supprimer(str(annonce.get("salon_id") or ""), str(annonce["message_id"]))
            await pb.maj("discord_annonces", annonce["id"], {"statut": "supprime"})
        except Exception as e:  # noqa: BLE001 — réessayé au tour suivant, l'erreur reste visible dans la base
            log.exception("Annonce %s non effacée", annonce.get("id"))
            await pb.maj("discord_annonces", annonce["id"], {"erreur": f"effacement : {type(e).__name__} : {e}"[:300]})
    return len(annonces)


def effaceur(client: discord.Client) -> Supprimer:
    async def supprimer(salon_id: str, message_id: str) -> None:
        salon = client.get_channel(int(salon_id)) or await client.fetch_channel(int(salon_id))
        try:
            await salon.get_partial_message(int(message_id)).delete()
        except discord.NotFound:
            pass  # déjà effacé à la main
    return supprimer


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
    envoyer, supprimer = envoyeur(client), effaceur(client)
    while not client.is_closed():
        try:
            await tour(envoyer, pb)
        except Exception:
            log.exception("Erreur dans la boucle des annonces du site")
        try:
            await tour_suppressions(supprimer, pb)
        except Exception:  # champ supprimer_le absent tant que scripts/setup-missives.mjs (site) n'a pas tourné
            log.debug("Effacement programmé des annonces indisponible", exc_info=True)
        await asyncio.sleep(ANNONCES_S)
