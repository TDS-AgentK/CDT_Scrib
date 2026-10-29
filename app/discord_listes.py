"""Copie des salons et des rôles du serveur dans la base du site (discord_salons, discord_roles), pour que la page
Économie propose des listes déroulantes, comme le tableau de bord de Draftbot. Synchronisation au démarrage, puis
après chaque création, modification ou suppression de salon ou de rôle (regroupée sur quelques secondes).
« acteur_voit » : le rôle Acteur (ROLE_ACTEUR_ID) peut voir le salon ; le site ne propose que ces salons pour les
fiches Discord. Calcul de Discord : permissions de @everyone et du rôle, remplacements du salon (ceux de sa catégorie
s'il est synchronisé) pour @everyone puis pour le rôle ; Administrateur voit tout.
"""
import asyncio
import logging
import os

import discord

from app.pocketbase import PocketBase, echapper

log = logging.getLogger("cdt_scrib.discord_listes")

TYPES_SALONS = {discord.ChannelType.text: "texte", discord.ChannelType.forum: "forum",
                discord.ChannelType.news: "annonces", discord.ChannelType.voice: "vocal"}
_en_attente: dict[int, asyncio.Task] = {}
ROLE_ACTEUR_ID = int(os.getenv("ROLE_ACTEUR_ID") or 871369772175085588)


def acteur_voit(salon, role: discord.Role | None) -> bool | None:
    """Le rôle peut-il voir le salon ? None si le rôle n'existe pas sur ce serveur (champ laissé tel quel)."""
    return None if role is None else salon.permissions_for(role).view_channel


async def _miroir(pb: PocketBase, collection: str, cle: str, guild_id: str, attendus: dict[str, dict]):
    """Crée, met à jour ou supprime les fiches pour qu'elles correspondent exactement à `attendus`."""
    existants = {r[cle]: r for r in await pb.lister(collection, f'guild_id="{echapper(guild_id)}"')}
    for ident, donnees in attendus.items():
        r = existants.get(ident)
        if not r:
            await pb.creer(collection, {"guild_id": guild_id, cle: ident, **donnees})
        elif any(r.get(k) != v for k, v in donnees.items()):
            await pb.maj(collection, r["id"], donnees)
    for ident, r in existants.items():
        if ident not in attendus:
            await pb.supprimer(collection, r["id"])


async def synchroniser(pb: PocketBase, guild: discord.Guild):
    gid = str(guild.id)
    salons = {}
    acteur = guild.get_role(ROLE_ACTEUR_ID)
    for c in guild.channels:
        if c.type in TYPES_SALONS:
            salons[str(c.id)] = {"nom": c.name, "type": TYPES_SALONS[c.type],
                                 "categorie": c.category.name if c.category else "", "position": c.position}
            if acteur is not None:
                salons[str(c.id)]["acteur_voit"] = acteur_voit(c, acteur)
    # « gerable » : le bot peut donner ce rôle (permission « Gérer les rôles » et rôle du bot placé au-dessus).
    peut = guild.me.guild_permissions.manage_roles
    roles = {str(r.id): {"nom": r.name, "couleur": f"#{r.color.value:06x}" if r.color.value else "", "position": r.position,
                         "gerable": bool(peut and r < guild.me.top_role)}
             for r in guild.roles if not r.is_default() and not r.managed}
    await _miroir(pb, "discord_salons", "salon_id", gid, salons)
    await _miroir(pb, "discord_roles", "role_id", gid, roles)
    vus = sum(1 for s in salons.values() if s.get("acteur_voit"))
    log.info("Listes Discord synchronisées : %d salons (%d visibles par Acteur), %d rôles (%s)", len(salons), vus, len(roles), guild.name)


def planifier(pb: PocketBase, guild: discord.Guild | None, delai: float = 5):
    """Synchronisation regroupée : plusieurs changements rapprochés ne déclenchent qu'une copie."""
    if guild is None:
        return
    ancienne = _en_attente.get(guild.id)
    if ancienne and not ancienne.done():
        ancienne.cancel()

    async def tache():
        await asyncio.sleep(delai)
        try:
            await synchroniser(pb, guild)
        except Exception:
            log.exception("Échec de la synchronisation des listes Discord")
    _en_attente[guild.id] = asyncio.create_task(tache())


def brancher(client: discord.Client, pb: PocketBase, guild_id: int | None):
    """Abonne le client aux évènements de salons et de rôles du serveur suivi (ou de tous s'il n'y en a pas)."""
    def suivi(guild):
        return guild is not None and (guild_id is None or guild.id == guild_id)

    async def sur_salon(salon, *args):
        if suivi(salon.guild):
            planifier(pb, salon.guild)

    async def sur_role(role, *args):
        if suivi(role.guild):
            planifier(pb, role.guild)

    for nom, fn in (("on_guild_channel_create", sur_salon), ("on_guild_channel_delete", sur_salon), ("on_guild_channel_update", sur_salon),
                    ("on_guild_role_create", sur_role), ("on_guild_role_delete", sur_role), ("on_guild_role_update", sur_role)):
        fn.__name__ = nom
        client.event(fn)

    def au_demarrage():
        for g in client.guilds:
            if suivi(g):
                planifier(pb, g, delai=1)
    return au_demarrage
