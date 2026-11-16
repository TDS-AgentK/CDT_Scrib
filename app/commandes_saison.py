"""Commandes saisonnières (demande de K du 16/11/2026) : Halloween (/halloween, /bonbons, /halloween-admin) et le calendrier
de l'Avent (/avent) n'existent dans le menu des commandes Discord, et dans /info, que pendant leur événement.

L'état vient des interrupteurs déjà réglés sur le site : boutique d'Halloween « ouverte » (hal_reglages) et calendrier de
l'Avent « ouvert » (avent_reglages). La déclaration est refaite au démarrage, puis vérifiée toutes les 10 minutes : une
commande est déclarée quand son événement s'ouvre et effacée de Discord quand il se ferme. Le code des commandes reste.
"""
import asyncio
import logging
import os

log = logging.getLogger("cdt_scrib.commandes_saison")

SAISONNIERES = {"halloween": ("halloween", "bonbons", "halloween-admin"), "avent": ("avent",)}
VERIFIER_TOUTES_S = 600


async def evenements_actifs(eco) -> set[str]:
    """Événements ouverts en ce moment (« halloween », « avent »)."""
    from app import avent, halloween
    actifs = set()
    try:
        if (await halloween.reglages(eco)).get("ouverte"):
            actifs.add("halloween")
    except Exception:
        log.exception("Réglages d'Halloween illisibles")
    try:
        if (await avent.reglages(eco)).get("ouvert"):
            actifs.add("avent")
    except Exception:
        log.exception("Réglages de l'Avent illisibles")
    return actifs


async def cachees(eco) -> set[str]:
    """Noms des commandes à cacher (événement fermé)."""
    actifs = await evenements_actifs(eco)
    return {c for ev, noms in SAISONNIERES.items() if ev not in actifs for c in noms}


async def synchroniser(client, eco, app_id: str, etat: dict):
    """Déclare ou efface les commandes saisonnières selon l'état des événements (seulement si l'état a changé)."""
    from scripts.register_commands import COMMANDS
    actifs = await evenements_actifs(eco)
    if etat.get("actifs") == actifs:
        return
    guild = os.getenv("GUILD_ID")
    http = client.http
    declarees = await (http.get_guild_commands(app_id, guild) if guild else http.get_global_commands(app_id))
    par_nom = {c.get("name"): c for c in declarees}
    for ev, noms in SAISONNIERES.items():
        for nom in noms:
            try:
                if ev in actifs:
                    commande = next(c for c in COMMANDS if c["name"] == nom)
                    await (http.upsert_guild_command(app_id, guild, commande) if guild else http.upsert_global_command(app_id, commande))
                elif nom in par_nom:
                    cid = par_nom[nom]["id"]
                    await (http.delete_guild_command(app_id, guild, cid) if guild else http.delete_global_command(app_id, cid))
                    log.info("Commande saisonnière cachée (événement fermé) : /%s", nom)
            except Exception:
                log.exception("Commande saisonnière /%s", nom)
    etat["actifs"] = actifs
    log.info("Commandes saisonnières : événements ouverts %s", sorted(actifs) or "aucun")


async def boucle(client, eco, app_id: str):
    await client.wait_until_ready()
    etat: dict = {}
    while not client.is_closed():
        try:
            await synchroniser(client, eco, app_id, etat)
        except Exception:
            log.exception("Commandes saisonnières : synchronisation")
        await asyncio.sleep(VERIFIER_TOUTES_S)
