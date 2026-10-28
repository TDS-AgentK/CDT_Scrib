"""Tests de /info : chaque commande enregistrée apparaît, et l'embed respecte les limites de Discord.
Lancer : python -m unittest discover -s tests
"""
import os
import unittest

os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_APPLICATION_ID", "1")

from app.info import vue_info  # noqa: E402
from scripts.register_commands import COMMANDS  # noqa: E402


class Info(unittest.TestCase):
    def test_toutes_les_commandes(self):
        texte = "\n".join(f["value"] for f in vue_info()["fields"])
        for c in COMMANDS:
            if c["name"] != "info":
                self.assertIn(f'**/{c["name"]}**', texte)

    def test_limites_discord(self):
        e = vue_info("!!")
        self.assertLessEqual(len(e["fields"]), 25)
        self.assertTrue(all(len(f["value"]) <= 1024 for f in e["fields"]))
        total = len(e["title"]) + len(e["description"]) + sum(len(f["name"]) + len(f["value"]) for f in e["fields"])
        self.assertLessEqual(total, 6000)
        self.assertIn("`!!rp @partenaires`", e["fields"][-1]["value"])
