"""Journal des mouvements que les autres historiques ne notent pas (collection `eco_journal`, créée par
scripts/setup-journal.mjs du site) : rôles Discord ajoutés ou retirés (par le bot comme à la main par le staff),
arrivées et départs du serveur, succès obtenus, niveaux atteints. La page Économie › Journal du site fusionne cette
collection avec eco_gains (Or, XP, monnaies), eco_mouvements (objets), eco_achats (boutique) et emb_tirages.

Rôles : un seul point d'entrée, l'évènement on_member_update (rôles avant / après), qui voit aussi les rôles donnés
à la main, par Draftbot ou par un boost. L'auteur et la raison viennent du journal d'audit du serveur (permission
« Voir les logs du serveur ») : un rôle donné par le bot porte la raison « Économie CDT — <origine> ». Sans cette
permission, la ligne est notée quand même, sans auteur. Une trace refusée ne bloque jamais l'opération.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord

from app.pocketbase import echapper

log = logging.getLogger("cdt_scrib.journal")

ATTENTE_AUDIT_S = 1.5  # l'entrée du journal d'audit arrive parfois juste après l'évènement
FENETRE_AUDIT = timedelta(seconds=30)


def _pb_date(d: datetime | None = None) -> str:
    return (d or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S.000Z")


async def noter(pb, type_: str, libelle: str, joueur_id: str = "", **autres):
    """Ajoute une ligne au journal ; n'interrompt jamais l'appelant."""
    ligne = {"type": type_, "libelle": str(libelle)[:300], "joueur": joueur_id or "", "date": _pb_date(), **autres}
    try:
        await pb.creer("eco_journal", ligne)
    except Exception:
        log.exception("Trace %s impossible : %s", type_, libelle)


async def auteur_et_raison(guild, membre_id: int, role_id: int, maintenant: datetime | None = None) -> tuple[str, str]:
    """(auteur, raison) de la dernière modification de rôle de ce membre qui touche ce rôle, d'après le journal d'audit."""
    maintenant = maintenant or datetime.now(timezone.utc)
    try:
        async for e in guild.audit_logs(limit=10, action=discord.AuditLogAction.member_role_update):
            if getattr(e.target, "id", None) != membre_id or maintenant - e.created_at > FENETRE_AUDIT:
                continue
            roles = list(getattr(e.after, "roles", None) or []) + list(getattr(e.before, "roles", None) or [])
            if any(getattr(r, "id", None) == role_id for r in roles):
                return (getattr(e.user, "display_name", "") or getattr(e.user, "name", "") or ""), (e.reason or "")
    except (discord.Forbidden, discord.HTTPException):
        pass
    return "", ""


async def _joueur(eco, membre) -> str:
    try:
        j = await eco.pb.premier("joueurs", f'discord_id="{echapper(membre.id)}"')
    except Exception:
        return ""
    return j["id"] if j else ""


def _membre(membre) -> dict:
    return {"discord_user_id": str(membre.id), "membre_nom": getattr(membre, "display_name", "") or getattr(membre, "name", "")}


async def roles_changes(eco, avant, apres, attente: float = ATTENTE_AUDIT_S):
    """Note chaque rôle ajouté ou retiré entre deux états d'un membre."""
    ajoutes = [r for r in apres.roles if r not in avant.roles]
    retires = [r for r in avant.roles if r not in apres.roles]
    if not ajoutes and not retires:
        return
    if attente:
        await asyncio.sleep(attente)
    joueur_id = await _joueur(eco, apres)
    for type_, role in [("role_ajout", r) for r in ajoutes] + [("role_retrait", r) for r in retires]:
        auteur, raison = await auteur_et_raison(apres.guild, apres.id, role.id)
        await noter(eco.pb, type_, role.name, joueur_id, role_id=str(role.id), auteur=auteur, motif=raison, **_membre(apres))


def brancher(client: discord.Client, eco):
    """Abonne le client aux changements de rôles, arrivées et départs du serveur suivi."""
    def suivi(guild):
        return guild is not None and (not eco.guild_id or guild.id == eco.guild_id)

    async def on_member_update(avant, apres):
        if suivi(apres.guild) and avant.roles != apres.roles:
            await roles_changes(eco, avant, apres)

    async def on_member_join(membre):
        if suivi(membre.guild):
            await noter(eco.pb, "arrivee", "arrivée sur le serveur", await _joueur(eco, membre), **_membre(membre))

    async def on_member_remove(membre):
        if suivi(membre.guild):
            await noter(eco.pb, "depart", "départ du serveur", await _joueur(eco, membre), **_membre(membre))

    for fn in (on_member_update, on_member_join, on_member_remove):
        client.event(fn)
