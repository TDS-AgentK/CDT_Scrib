"""Rôles gagnés sur le site (file discord_roles_a_donner, ex. récompense d'un record de quiz).
Lancer : python -m unittest discover -s tests
"""
import asyncio
import unittest
from datetime import datetime, timezone

from app import roles_site
from tests.test_loteries_roles import VIP, HAUT, FausseBase, economie, guilde, membre

MAINTENANT = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)


class Base(FausseBase):
    def __init__(self, joueurs=None, demandes=None):
        super().__init__()
        self.joueurs = joueurs or {}
        self.demandes = {d["id"]: dict(d) for d in (demandes or [])}

    async def premier(self, col, filtre):
        if col == "joueurs":
            return next((j for i, j in self.joueurs.items() if f'id="{i}"' in filtre), None)
        return await super().premier(col, filtre)

    async def lister(self, col, filtre=None, tri=None, expand=None):
        assert col == "discord_roles_a_donner" and filtre == 'statut="en_attente"'
        return [dict(d) for d in self.demandes.values() if d.get("statut") == "en_attente"]

    async def maj(self, col, id_, d):
        await super().maj(col, id_, d)
        if col == "discord_roles_a_donner":
            self.demandes[id_].update(d)


def demande(id_, role="50", heures=0, joueur="j7", discord_id="7"):
    return {"id": id_, "joueur": joueur, "discord_id": discord_id, "role_id": role, "role_nom": "VIP",
            "role_heures": heures, "motif": "Record quiz : Test", "statut": "en_attente"}


class RolesSite(unittest.TestCase):
    def test_donne_le_role_definitif(self):
        m = membre(7)
        g = guilde(membres={7: m})
        pb = Base({"j7": {"id": "j7", "discord_id": "7"}}, [demande("d1")])
        e = economie(pb, g)
        e.guild_id = 999
        self.assertEqual(asyncio.run(roles_site.tour(e)), 1)
        self.assertIn(VIP, m.roles)
        self.assertEqual(pb.demandes["d1"]["statut"], "donne")
        self.assertEqual(pb.temp, {})  # définitif : pas de rôle temporaire noté

    def test_temporaire_note_avec_le_motif(self):
        m = membre(7)
        g = guilde(membres={7: m})
        pb = Base({"j7": {"id": "j7", "discord_id": "7"}}, [demande("d1", heures=48)])
        e = economie(pb, g)
        maj = asyncio.run(roles_site.traiter(e, g, pb.demandes["d1"], MAINTENANT))
        self.assertEqual(maj["statut"], "donne")
        t = next(iter(pb.temp.values()))
        self.assertEqual((t["role_id"], t["origine"]), ("50", "Record quiz : Test"))

    def test_echec_note_la_raison_et_ne_relance_pas(self):
        g = guilde(membres={7: membre(7)})
        pb = Base({"j7": {"id": "j7", "discord_id": "7"}}, [demande("d1", role="60"), demande("d2", joueur="", discord_id="")])
        e = economie(pb, g)
        e.guild_id = 999
        asyncio.run(roles_site.tour(e))
        self.assertEqual(pb.demandes["d1"]["statut"], "erreur")
        self.assertIn("au-dessus", pb.demandes["d1"]["erreur"])
        self.assertIn("pas de compte Discord", pb.demandes["d2"]["erreur"])
        self.assertEqual(asyncio.run(roles_site.tour(e)), 0)
        self.assertNotIn(HAUT, g.get_member(7).roles)


if __name__ == "__main__":
    unittest.main()
