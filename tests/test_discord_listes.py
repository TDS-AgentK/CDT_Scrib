"""Salons visibles par le rôle Acteur (discord_listes.acteur_voit et synchroniser), sur un faux serveur discord.py.
Lancer : python -m unittest discover -s tests
"""
import asyncio
import unittest
from unittest.mock import MagicMock, PropertyMock, patch

import discord

from app import discord_listes

GUILDE, ACTEUR, AUTRE = 1000, discord_listes.ROLE_ACTEUR_ID, 2000
VOIR = discord.Permissions(view_channel=True).value


def remplacement(id_, autoriser=0, refuser=0, type_=0):
    return {"id": str(id_), "type": type_, "allow": str(autoriser), "deny": str(refuser)}


def serveur(base_everyone=VOIR, base_acteur=0, acteur=True, salons=()):
    roles = [{"id": str(GUILDE), "name": "@everyone", "permissions": str(base_everyone), "position": 0, "color": 0,
              "hoist": False, "managed": False, "mentionable": False}]
    if acteur:
        roles.append({"id": str(ACTEUR), "name": "Acteur", "permissions": str(base_acteur), "position": 2, "color": 0,
                      "hoist": False, "managed": False, "mentionable": False})
    roles.append({"id": str(AUTRE), "name": "Autre", "permissions": "0", "position": 1, "color": 0,
                  "hoist": False, "managed": False, "mentionable": False})
    etat = MagicMock()
    etat.self_id = 1
    etat.member_cache_flags = discord.MemberCacheFlags.none()
    g = discord.Guild(data={"id": str(GUILDE), "name": "Test", "roles": roles, "channels": list(salons),
                            "emojis": [], "stickers": [], "features": [], "owner_id": "1"}, state=etat)
    return g


def salon(id_, nom, parent=None, remplacements=(), type_=0, position=0):
    d = {"id": str(id_), "name": nom, "type": type_, "position": position, "permission_overwrites": list(remplacements),
         "guild_id": str(GUILDE)}
    if parent:
        d["parent_id"] = str(parent)
    return d


class FausseBase:
    def __init__(self):
        self.crees = {}

    async def lister(self, col, filtre=None):
        return []

    async def creer(self, col, d):
        self.crees.setdefault(col, []).append(d)


def visibles(g):
    return {c.name: discord_listes.acteur_voit(c, g.get_role(ACTEUR)) for c in g.channels if c.type != discord.ChannelType.category}


class ActeurVoit(unittest.TestCase):
    def test_permissions_et_remplacements(self):
        g = serveur(base_everyone=0, salons=[
            salon(1, "Côté joueur", type_=4, remplacements=[remplacement(ACTEUR, autoriser=VOIR)]),
            salon(2, "synchro-categorie", parent=1, remplacements=[remplacement(ACTEUR, autoriser=VOIR)]),
            salon(3, "acteur-refuse", parent=1, remplacements=[remplacement(ACTEUR, refuser=VOIR)]),
            salon(4, "everyone-seul", remplacements=[remplacement(GUILDE, autoriser=VOIR)]),
            salon(5, "everyone-refuse-acteur-autorise", remplacements=[remplacement(GUILDE, refuser=VOIR), remplacement(ACTEUR, autoriser=VOIR)]),
            salon(6, "rien"),
            salon(7, "autre-role-seulement", remplacements=[remplacement(AUTRE, autoriser=VOIR)]),
        ])
        self.assertEqual(visibles(g), {"synchro-categorie": True, "acteur-refuse": False, "everyone-seul": True,
                                       "everyone-refuse-acteur-autorise": True, "rien": False, "autre-role-seulement": False})

    def test_everyone_refuse_dans_le_salon_bat_la_base_du_role(self):
        g = serveur(base_everyone=VOIR, base_acteur=VOIR, salons=[salon(1, "staff", remplacements=[remplacement(GUILDE, refuser=VOIR)])])
        self.assertEqual(visibles(g), {"staff": False})

    def test_administrateur_voit_tout(self):
        g = serveur(base_everyone=0, base_acteur=discord.Permissions(administrator=True).value,
                    salons=[salon(1, "staff", remplacements=[remplacement(ACTEUR, refuser=VOIR)])])
        self.assertEqual(visibles(g), {"staff": True})

    def test_role_absent(self):
        g = serveur(acteur=False, salons=[salon(1, "general")])
        self.assertIsNone(discord_listes.acteur_voit(g.get_channel(1), g.get_role(ACTEUR)))

    def test_synchroniser_ecrit_acteur_voit(self):
        g = serveur(base_everyone=0, salons=[salon(1, "public", remplacements=[remplacement(ACTEUR, autoriser=VOIR)]), salon(2, "staff")])
        moi = MagicMock(); moi.guild_permissions = discord.Permissions.none()
        pb = FausseBase()
        with patch.object(discord.Guild, "me", new_callable=PropertyMock, return_value=moi):
            asyncio.run(discord_listes.synchroniser(pb, g))
        par_nom = {d["nom"]: d.get("acteur_voit") for d in pb.crees["discord_salons"]}
        self.assertEqual(par_nom, {"public": True, "staff": False})


if __name__ == "__main__":
    unittest.main()
