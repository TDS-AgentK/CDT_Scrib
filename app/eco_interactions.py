"""Commandes slash de l'économie et clics sur ses menus/boutons (custom_id « eco:… »), reçus par /interactions.

Discord exige une réponse en moins de 3 s : on répond « en cours » tout de suite (réponse différée), puis on
remplace le message par le vrai contenu via le webhook de l'interaction.
"""
import logging

import discord
import httpx

from app import eco_vues
from app.discord_types import EPHEMERAL_FLAG

log = logging.getLogger("cdt_scrib.eco_interactions")

COMMANDES = {"profil", "inventaire", "boutique", "classement"}
DIFFERE_MESSAGE, DIFFERE_MAJ, MAJ_MESSAGE = 5, 6, 7


async def _modifier(app_id: str, jeton: str, embeds: list[dict], composants: list | None = None):
    url = f"https://discord.com/api/v10/webhooks/{app_id}/{jeton}/messages/@original"
    corps = {"content": "", "embeds": embeds, "components": composants or []}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(url, json=corps, timeout=10)
            if r.status_code >= 400:
                log.error("Échec de la mise à jour du message : %s %s", r.status_code, r.text)
    except httpx.HTTPError as exc:
        log.error("Erreur réseau lors de la mise à jour du message : %s", exc)


async def _membre(eco, payload: dict, user_id: str | None = None) -> discord.Member | None:
    guild = eco.client.get_guild(int(payload["guild_id"])) if payload.get("guild_id") else None
    if not guild:
        return None
    uid = int(user_id or payload["member"]["user"]["id"])
    m = guild.get_member(uid)
    if m:
        return m
    try:
        return await guild.fetch_member(uid)
    except discord.HTTPException:
        return None


def reponse_immediate(payload: dict) -> dict:
    """Réponse à renvoyer tout de suite (avant le traitement en arrière-plan)."""
    if payload["type"] == 2:  # commande slash
        prive = payload["data"]["name"] in ("inventaire",)
        return {"type": DIFFERE_MESSAGE, "data": {"flags": EPHEMERAL_FLAG} if prive else {}}
    cid = payload["data"]["custom_id"]
    if cid == "eco:no":
        return {"type": MAJ_MESSAGE, "data": {"content": "", "embeds": [{"description": "Achat annulé.", "color": 0x99AAB5}], "components": []}}
    if cid.startswith("eco:buy:"):
        return {"type": DIFFERE_MAJ}
    # Changer de boutique / choisir un article : réponse privée à la personne qui clique.
    return {"type": DIFFERE_MESSAGE, "data": {"flags": EPHEMERAL_FLAG}}


async def traiter(eco, payload: dict, app_id: str):
    """Traitement en arrière-plan d'une commande slash ou d'un clic de composant de l'économie."""
    jeton = payload["token"]
    try:
        if payload["type"] == 2:
            await _commande(eco, payload, app_id, jeton)
        else:
            await _composant(eco, payload, app_id, jeton)
    except Exception:
        log.exception("Erreur pendant une interaction de l'économie")
        await _modifier(app_id, jeton, [eco_vues.erreur("Une erreur est survenue, réessaie dans un instant.")])


async def _commande(eco, payload: dict, app_id: str, jeton: str):
    data = payload["data"]
    nom = data["name"]
    membre = await _membre(eco, payload)
    if not membre:
        await _modifier(app_id, jeton, [eco_vues.erreur("Commande à utiliser sur le serveur.")])
        return
    options = {o["name"]: o.get("value") for o in data.get("options", [])}
    if nom == "profil":
        cible = membre
        if options.get("membre") and str(options["membre"]) != str(membre.id):
            cfg = await eco.config()
            if not cfg["reglages"].get("voir_niveau_autres"):
                await _modifier(app_id, jeton, [eco_vues.erreur("Le niveau des autres membres n'est pas visible.")])
                return
            cible = await _membre(eco, payload, options["membre"]) or membre
        await _modifier(app_id, jeton, [await eco_vues.vue_profil(eco, cible)])
    elif nom == "inventaire":
        await _modifier(app_id, jeton, await eco_vues.vue_inventaire(eco, membre))
    elif nom == "classement":
        await _modifier(app_id, jeton, [await eco_vues.vue_classement(eco)])
    elif nom == "boutique":
        embed, composants = await eco_vues.vue_boutique(eco, membre)
        await _modifier(app_id, jeton, [embed], composants)


async def _composant(eco, payload: dict, app_id: str, jeton: str):
    data = payload["data"]
    cid, valeurs = data["custom_id"], data.get("values") or []
    membre = await _membre(eco, payload)
    if not membre:
        await _modifier(app_id, jeton, [eco_vues.erreur("Action à utiliser sur le serveur.")])
        return
    if cid == "eco:bq":
        embed, composants = await eco_vues.vue_boutique(eco, membre, valeurs[0] if valeurs else None)
        await _modifier(app_id, jeton, [embed], composants)
    elif cid.startswith("eco:art:"):
        embed, composants = await eco_vues.vue_confirmation(eco, membre, cid.split(":")[2], valeurs[0])
        await _modifier(app_id, jeton, [embed], composants)
    elif cid.startswith("eco:buy:"):
        _, _, boutique_id, article_id = cid.split(":")
        cfg = await eco.config()
        boutique = next((b for b in cfg["boutiques"] if b["id"] == boutique_id), None)
        article = next((a for a in cfg["articles"] if a["id"] == article_id), None)
        if not boutique or not article:
            await _modifier(app_id, jeton, [eco_vues.resultat_achat(False, "Cet article n'est plus disponible.")])
            return
        canal = payload.get("channel_id")
        origine = f'https://discord.com/channels/{payload["guild_id"]}/{canal}' if canal else ""
        ok, texte = await eco.acheter(membre, boutique, article, origine)
        await _modifier(app_id, jeton, [eco_vues.resultat_achat(ok, texte)])
