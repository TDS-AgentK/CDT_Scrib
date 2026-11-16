"""Tests de /info : chaque commande enregistrée apparaît, et l'embed respecte les limites de Discord.
Lancer : python -m unittest discover -s tests
"""
import os
import unittest

os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_APPLICATION_ID", "1")

from app.info import ADMIN, vue_info, vue_infoadmin  # noqa: E402
from scripts.register_commands import COMMANDS  # noqa: E402


class Info(unittest.TestCase):
    def test_toutes_les_commandes(self):
        """Chaque commande est dans /info, ou dans /infoadmin pour les commandes admin (/infoadmin elle-même exceptée)."""
        info = "\n".join(f["value"] for f in vue_info()["fields"])
        admin = "\n".join(f["value"] for f in vue_infoadmin()["fields"])
        for c in COMMANDS:
            if c["name"] in ("info", "infoadmin"):
                continue
            if c["name"] in ADMIN:
                self.assertIn(f'**/{c["name"]}**', admin)
                self.assertNotIn(f'**/{c["name"]}**', info)
            else:
                self.assertIn(f'**/{c["name"]}**', info)

    def test_evenements_fermes_caches(self):
        cachees = {"halloween", "bonbons", "halloween-admin", "avent"}
        info = "\n".join(f["value"] for f in vue_info("??", cachees)["fields"])
        admin = "\n".join(f["value"] for f in vue_infoadmin(cachees)["fields"])
        for nom in cachees:
            self.assertNotIn(f"**/{nom}**", info + admin)
        self.assertNotIn("Halloween", "".join(f["name"] for f in vue_info("??", cachees)["fields"]))

    def test_infoadmin_cachee_aux_non_admins(self):
        c = next(c for c in COMMANDS if c["name"] == "infoadmin")
        self.assertEqual(c["default_member_permissions"], "8")  # Administrateur

    def test_limites_discord(self):
        e = vue_info("!!")
        self.assertLessEqual(len(e["fields"]), 25)
        self.assertTrue(all(len(f["value"]) <= 1024 for f in e["fields"]))
        total = len(e["title"]) + len(e["description"]) + sum(len(f["name"]) + len(f["value"]) for f in e["fields"])
        self.assertLessEqual(total, 6000)
        self.assertIn("`!!rp @partenaires`", e["fields"][-1]["value"])
