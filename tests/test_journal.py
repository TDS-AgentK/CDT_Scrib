"""Journal des rôles, arrivées / départs, succès et niveaux (collection eco_journal).
Lancer : python -m unittest discover -s tests
"""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord

from app import journal

MAINTENANT = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
VIP = SimpleNamespace(id=50, name="VIP")
ROI = SimpleNamespace(id=60, name="Roi / Reine")


class Base:
    def __init__(self, joueurs=None, refuse=False):
        self.lignes, self.joueurs, self.refuse = [], joueurs or {}, refuse

    async def premier(self, col, filtre):
        assert col == "joueurs"
        return next((j for d, j in self.joueurs.items() if f'discord_id="{d}"' in filtre), None)

    async def creer(self, col, d):
        assert col == "eco_journal"
        if self.refuse:
            raise RuntimeError("PocketBase POST -> 404")
        self.lignes.append(d)
        return d


def guilde(entrees=(), interdit=False):
    async def audit_logs(limit=10, action=None):
        if interdit:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Access")
        for e in entrees:
            yield e
    return SimpleNamespace(id=999, audit_logs=audit_logs)


def membre(roles, g, id_=7, nom="Kyanite"):
    return SimpleNamespace(id=id_, display_name=nom, roles=list(roles), guild=g)


def entree(cible, role, auteur="Scrib", raison="Économie CDT — achat", il_y_a=2):
    return SimpleNamespace(target=SimpleNamespace(id=cible), user=SimpleNamespace(display_name=auteur), reason=raison,
                           created_at=datetime.now(timezone.utc) - timedelta(seconds=il_y_a),
                           before=SimpleNamespace(roles=[]), after=SimpleNamespace(roles=[role]))


class Journal(unittest.TestCase):
    def eco(self, pb):
        return SimpleNamespace(pb=pb, guild_id=999)

    def test_role_ajoute_avec_auteur_et_raison(self):
        g = guilde([entree(7, VIP)])
        pb = Base({"7": {"id": "j7"}})
        asyncio.run(journal.roles_changes(self.eco(pb), membre([], g), membre([VIP], g), attente=0))
        self.assertEqual(len(pb.lignes), 1)
        l = pb.lignes[0]
        self.assertEqual((l["type"], l["libelle"], l["joueur"], l["role_id"]), ("role_ajout", "VIP", "j7", "50"))
        self.assertEqual((l["auteur"], l["motif"], l["discord_user_id"], l["membre_nom"]), ("Scrib", "Économie CDT — achat", "7", "Kyanite"))

    def test_role_retire_a_la_main_sans_permission_d_audit(self):
        g = guilde(interdit=True)
        pb = Base()
        asyncio.run(journal.roles_changes(self.eco(pb), membre([VIP, ROI], g), membre([VIP], g), attente=0))
        self.assertEqual([(l["type"], l["libelle"], l["auteur"], l["joueur"]) for l in pb.lignes], [("role_retrait", "Roi / Reine", "", "")])

    def test_audit_d_un_autre_membre_ou_trop_ancien_ignore(self):
        g = guilde([entree(8, VIP, auteur="Autre"), entree(7, VIP, auteur="Ancien", il_y_a=600)])
        self.assertEqual(asyncio.run(journal.auteur_et_raison(g, 7, 50)), ("", ""))

    def test_rien_si_les_roles_ne_changent_pas(self):
        g = guilde()
        pb = Base()
        asyncio.run(journal.roles_changes(self.eco(pb), membre([VIP], g), membre([VIP], g), attente=0))
        self.assertEqual(pb.lignes, [])

    def test_trace_refusee_ne_bloque_pas(self):
        asyncio.run(journal.noter(Base(refuse=True), "succes", "Premier pas", "j7"))  # ne lève pas


if __name__ == "__main__":
    unittest.main()
