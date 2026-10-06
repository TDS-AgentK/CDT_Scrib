"""Annonce quotidienne des anniversaires et décès des personnages (remplace l'agenda Google « Anniversaires du Temps »).

Réglages : collection anniv_reglages (fiche « general »), page Économie › Anniversaires du site. Chaque jour, à partir
de l'heure choisie (heure de Paris), le bot lit /anniversaires.json sur le site (mêmes règles que la page Calendrier :
anniversaire des personnages vivants, seulement la date de décès pour les morts) et poste un embed par personnage.
Le jour annoncé est noté dans derniere_date : une seule annonce par jour, même après un redémarrage.
"""
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
import httpx

from app.pocketbase import PocketBase

log = logging.getLogger("cdt_scrib.anniversaires")

PARIS = ZoneInfo("Europe/Paris")
OR, GRIS = 0xC5A24F, 0x8A8A8A
VERIFIER_TOUTES_S = 300


class _Valeurs(dict):
    def __missing__(self, cle):  # accolade inconnue laissée telle quelle
        return "{" + cle + "}"


def _titre(modele: str, e: dict) -> str:
    try:
        return (modele or e["titre"]).format_map(_Valeurs(nom=e["nom"], date=e["date"], annees=e["annees"]))[:256]
    except (ValueError, IndexError):
        return e["titre"][:256]


def embeds_du_jour(reglages: dict, evenements: list[dict]) -> list[discord.Embed]:
    embeds = []
    for e in evenements:
        deces = e["type"] == "deces"
        modele = reglages.get("modele_deces" if deces else "modele_anniversaire") or ""
        em = discord.Embed(title=_titre(modele, e), url=e.get("lien") or None, color=GRIS if deces else OR)
        if e.get("image"):
            em.set_thumbnail(url=e["image"])
        embeds.append(em)
    return embeds


async def annoncer(client: discord.Client, pb: PocketBase, reglages: dict, jour: str) -> int:
    """Poste les annonces du jour (AAAA-MM-JJ) ; renvoie le nombre de personnages annoncés."""
    site = (reglages.get("lien_site") or "").rstrip("/")
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.get(f"{site}/anniversaires.json", params={"date": jour})
        r.raise_for_status()
        evenements = r.json().get("evenements", [])
    if evenements:
        salon = client.get_channel(int(reglages["salon_id"])) or await client.fetch_channel(int(reglages["salon_id"]))
        mention = f"<@&{reglages['role_id']}>" if reglages.get("role_id") else None
        embeds = embeds_du_jour(reglages, evenements)
        for i in range(0, len(embeds), 10):  # 10 embeds au plus par message
            await salon.send(content=mention if i == 0 else None, embeds=embeds[i:i + 10],
                             allowed_mentions=discord.AllowedMentions(roles=True))
    await pb.maj("anniv_reglages", reglages["id"], {"derniere_date": jour})
    return len(evenements)


async def boucle(client: discord.Client, pb: PocketBase):
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            reglages = await pb.premier("anniv_reglages", 'cle="general"')
            maintenant = datetime.now(PARIS)
            jour = maintenant.strftime("%Y-%m-%d")
            if (reglages and reglages.get("actif") and reglages.get("salon_id") and reglages.get("lien_site")
                    and maintenant.hour >= int(reglages.get("heure") or 0) and reglages.get("derniere_date") != jour):
                n = await annoncer(client, pb, reglages, jour)
                log.info("Anniversaires du %s : %d annonce(s)", jour, n)
        except Exception:
            log.exception("Erreur dans l'annonce des anniversaires")
        await asyncio.sleep(VERIFIER_TOUTES_S)
