"""Rôles Discord recopiés dans la fiche joueur (bonus de rôle des missives du site).
Lancer : python -m unittest discover -s tests
"""
import asyncio
import unittest
from types import SimpleNamespace

from app import journal, roles_joueurs
from tests.test_loteries_roles import BOT, HAUT, VIP, Role, guilde, membre

EVERYONE = Role(999, "@everyone", 0, defaut=True)
J7 = "123456789012345678"


class Base:
    def __init__(self, joueurs):
        self.joueurs = {j["id"]: dict(j) for j in joueurs}
        self.maj_faites = []

    async def lister(self, col, filtre=None, tri=None, expand=None):
        assert col == "joueurs"
        return [dict(j) for j in self.joueurs.values()]

    async def maj(self, col, id_, d):
        assert col == "joueurs"
        self.maj_faites.append((id_, d))
        self.joueurs[id_].update(d)
        return self.joueurs[id_]


def eco(pb, g=None):
    async def joueur_de(user):
        return next((dict(j) for j in pb.joueurs.values() if j.get("discord_id") == str(user.id)), None)
    return SimpleNamespace(pb=pb, joueur_de=joueur_de, guild_id=999, client=SimpleNamespace(get_guild=lambda i: g))


class RolesJoueurs(unittest.TestCase):
    def test_roles_de_sans_everyone_du_plus_haut_au_plus_bas(self):
        m = membre(7, [EVERYONE, VIP, HAUT])
        self.assertEqual(roles_joueurs.roles_de(m), [{"id": "60", "nom": "Grand Maître"}, {"id": "50", "nom": "VIP"}])
        self.assertEqual(roles_joueurs.roles_de(None), [])

    def test_noter_ecrit_puis_ne_reecrit_pas(self):
        pb = Base([{"id": "j7", "discord_id": "7"}])
        e = eco(pb)
        m = membre(7, [EVERYONE, VIP])
        self.assertTrue(asyncio.run(roles_joueurs.noter(e, m)))
        self.assertEqual(pb.joueurs["j7"]["roles_discord"], [{"id": "50", "nom": "VIP"}])
        self.assertTrue(pb.joueurs["j7"]["roles_discord_maj"])
        self.assertFalse(asyncio.run(roles_joueurs.noter(e, m)), "mêmes rôles : fiche laissée telle quelle")
        self.assertEqual(len(pb.maj_faites), 1)

    def test_noter_sans_fiche_ni_erreur(self):
        pb = Base([])
        self.assertFalse(asyncio.run(roles_joueurs.noter(eco(pb), membre(7, [VIP]))))

    def test_erreur_de_base_ne_remonte_pas(self):
        pb = Base([{"id": "j7", "discord_id": "7"}])

        async def maj(*a):
            raise RuntimeError("champ roles_discord absent")
        pb.maj = maj
        self.assertFalse(asyncio.run(roles_joueurs.noter(eco(pb), membre(7, [VIP]))))

    def test_tout_copier_joueur_parti_et_id_invalide(self):
        m = membre(int(J7), [EVERYONE, HAUT, VIP])
        g = guilde(membres={int(J7): m})
        pb = Base([
            {"id": "a", "discord_id": J7},
            {"id": "b", "discord_id": "223456789012345678", "roles_discord": [{"id": "50", "nom": "VIP"}]},  # parti
            {"id": "c", "discord_id": "1"},  # ID laissé par l'import : ignoré
        ])
        self.assertEqual(asyncio.run(roles_joueurs.tout_copier(eco(pb, g), g)), 2)
        self.assertEqual([r["id"] for r in pb.joueurs["a"]["roles_discord"]], ["60", "50"])
        self.assertEqual(pb.joueurs["b"]["roles_discord"], [])
        self.assertNotIn("roles_discord", pb.joueurs["c"])

    def test_changement_de_role_recopie_par_le_journal(self):
        pb = Base([{"id": "j7", "discord_id": "7"}])
        e = eco(pb)
        e.guild_id = None
        client = SimpleNamespace(evenements={})
        client.event = lambda fn: client.evenements.__setitem__(fn.__name__, fn)
        notes = []

        async def roles_changes(*a, **k):
            notes.append(a)
        ancien, journal.roles_changes = journal.roles_changes, roles_changes
        try:
            journal.brancher(client, e)
            avant, apres = membre(7, [EVERYONE]), membre(7, [EVERYONE, BOT])
            apres.guild = SimpleNamespace(id=999)
            asyncio.run(client.evenements["on_member_update"](avant, apres))
        finally:
            journal.roles_changes = ancien
        self.assertEqual(pb.joueurs["j7"]["roles_discord"], [{"id": "1", "nom": "Bot"}])
        self.assertEqual(len(notes), 1, "le journal note toujours le changement")


if __name__ == "__main__":
    unittest.main()
