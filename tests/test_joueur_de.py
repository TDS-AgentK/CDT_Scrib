"""Fiche joueur d'un membre Discord : un ID invalide (« 1 » de l'import, cas de Ràlas) est remplacé par le vrai.
Lancer : python -m unittest discover -s tests
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from app.economie import Economie

MEMBRE = SimpleNamespace(id=412345678901234567, name="ralas_arnors")


def chercher(fiche: dict | None):
    maj = mock.AsyncMock(side_effect=lambda c, i, d: {**fiche, **d})

    async def premier(col, filtre):
        if filtre.startswith("discord_id="):
            return fiche if fiche and f'"{fiche.get("discord_id")}"' in filtre else None
        return fiche
    eco = SimpleNamespace(pb=SimpleNamespace(premier=premier, maj=maj))
    return asyncio.run(Economie.joueur_de(eco, MEMBRE)), maj


class JoueurDe(unittest.TestCase):
    def test_id_invalide_remplace(self):
        j, maj = chercher({"id": "jR", "pseudo_discord": "ralas_arnors", "discord_id": "1"})
        maj.assert_awaited_once_with("joueurs", "jR", {"discord_id": "412345678901234567"})
        self.assertEqual(j["discord_id"], "412345678901234567")

    def test_id_vide_enregistre(self):
        j, maj = chercher({"id": "jR", "pseudo_discord": "ralas_arnors", "discord_id": ""})
        maj.assert_awaited_once()

    def test_vrai_id_jamais_ecrase(self):
        j, maj = chercher({"id": "jR", "pseudo_discord": "ralas_arnors", "discord_id": "286482667023892480"})
        maj.assert_not_awaited()

    def test_trouve_par_id(self):
        j, maj = chercher({"id": "jR", "pseudo_discord": "autre", "discord_id": "412345678901234567"})
        self.assertEqual(j["id"], "jR")
        maj.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
