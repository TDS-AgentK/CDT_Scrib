"""Annonce quotidienne des anniversaires et décès des personnages, et des autres événements (remplace l'agenda Google
« Anniversaires du Temps »).

Réglages : collection anniv_reglages (fiche « general ») et anniv_evenements, page Économie › Anniversaires du site.
Chaque jour, à partir de l'heure choisie (heure de Paris), le bot lit /anniversaires.json sur le site (mêmes règles que
la page Calendrier : anniversaire des personnages vivants, seulement la date de décès pour les morts) et poste un
message par personnage, puis un par autre événement. Les messages passent par un webhook du salon pour prendre le nom
et l'avatar propres à chaque type (Chroniqueur des naissances, des pompes funèbres, Crieur public) ; sans le droit
« Gérer les webhooks », le bot poste lui-même, en précédant le message de ce nom.
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
VERIFIER_TOUTES_S = 300
NOM_WEBHOOK = "Annonces des Chroniques"


class _Valeurs(dict):
    def __missing__(self, cle):  # accolade inconnue laissée telle quelle
        return "{" + cle + "}"


def remplir(modele: str, **valeurs) -> str:
    try:
        return modele.format_map(_Valeurs(**valeurs))[:2000]
    except (ValueError, IndexError):
        return modele[:2000]


def messages_du_jour(reglages: dict, donnees: dict) -> list[tuple[str, str, str]]:
    """(texte, nom de l'expéditeur, avatar) de chaque annonce, dans l'ordre : anniversaires, décès, autres."""
    domaine = (reglages.get("lien_fiches") or reglages.get("lien_site") or "").rstrip("/")
    out = []
    for e in donnees.get("evenements", []):
        deces = e["type"] == "deces"
        lien = f"{domaine}{e['chemin']}" if e.get("chemin") else ""
        modele = reglages.get("modele_deces" if deces else "modele_anniversaire") or e["titre"]
        texte = remplir(modele, nom=e["nom"], lien=lien, date=e["date"], annees=e["annees"])
        cle = "deces" if deces else "naissances"
        out.append((texte, reglages.get(f"nom_{cle}") or "", reglages.get(f"avatar_{cle}") or ""))
    for a in donnees.get("autres", []):
        out.append((remplir(a["texte"], annees=a["annees"]), reglages.get("nom_crieur") or "", reglages.get("avatar_crieur") or ""))
    return [m for m in out if m[0].strip()]


async def _webhook(client: discord.Client, salon) -> discord.Webhook | None:
    try:
        for w in await salon.webhooks():
            if w.user and client.user and w.user.id == client.user.id and w.name == NOM_WEBHOOK:
                return w
        return await salon.create_webhook(name=NOM_WEBHOOK, reason="Annonces des anniversaires (CDT)")
    except discord.Forbidden:
        log.warning("Pas le droit « Gérer les webhooks » dans #%s : annonces postées par le bot lui-même.", salon)
        return None


async def annoncer(client: discord.Client, pb: PocketBase, reglages: dict, jour: str) -> int:
    """Poste les annonces du jour (AAAA-MM-JJ) ; renvoie le nombre de messages."""
    site = (reglages.get("lien_site") or "").rstrip("/")
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.get(f"{site}/anniversaires.json", params={"date": jour})
        r.raise_for_status()
        messages = messages_du_jour(reglages, r.json())
    if messages:
        salon = client.get_channel(int(reglages["salon_id"])) or await client.fetch_channel(int(reglages["salon_id"]))
        webhook = await _webhook(client, salon)
        mention = f"<@&{reglages['role_id']}>\n" if reglages.get("role_id") else ""
        for i, (texte, nom, avatar) in enumerate(messages):
            contenu = (mention if i == 0 else "") + texte
            if webhook:
                await webhook.send(contenu, username=nom or None, avatar_url=avatar or None,
                                   allowed_mentions=discord.AllowedMentions(roles=True))
            else:
                await salon.send((f"**{nom}**\n" if nom else "") + contenu, allowed_mentions=discord.AllowedMentions(roles=True))
    await pb.maj("anniv_reglages", reglages["id"], {"derniere_date": jour})
    return len(messages)


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
