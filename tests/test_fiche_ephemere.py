"""Tests de la suppression automatique des messages de /fiche voir (24 h, y compris après redémarrage).
Lancer : python -m unittest discover -s tests
"""
import asyncio
import os
import tempfile
import unittest

import httpx

from app import fiche_ephemere
from app.database import Database

T0 = 1_000_000


def _run(coro):
    return asyncio.run(coro)


class FicheEphemereTest(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.chemin = os.path.join(self.dossier.name, "fiches.db")

    def tearDown(self):
        self.dossier.cleanup()

    def test_supprime_apres_24h_meme_apres_redemarrage(self):
        async def scenario():
            db = Database(self.chemin)
            await fiche_ephemere.programmer(db, {"id": "m1", "channel_id": "c1"}, maintenant=T0)
            await fiche_ephemere.programmer(db, {"id": "m2", "channel_id": "c1"}, maintenant=T0 + 60)
            await db.close()

            db = Database(self.chemin)  # redémarrage : nouvelle connexion, la ligne est toujours là
            supprimes = []

            async def supprimer(c, m):
                supprimes.append((c, m))
                return True

            self.assertEqual(await fiche_ephemere.balayer(db, supprimer, maintenant=T0 + 24 * 3600 - 1), 0)
            self.assertEqual(await fiche_ephemere.balayer(db, supprimer, maintenant=T0 + 24 * 3600), 1)
            self.assertEqual(supprimes, [("c1", "m1")])
            self.assertEqual(await fiche_ephemere.balayer(db, supprimer, maintenant=T0 + 24 * 3600 + 60), 1)
            self.assertEqual(await db.suppressions_dues(T0 + 10 * 24 * 3600), [])
            await db.close()

        _run(scenario())

    def test_echec_temporaire_retente(self):
        async def scenario():
            db = Database(self.chemin)
            await fiche_ephemere.programmer(db, {"id": "m1", "channel_id": "c1"}, maintenant=T0)

            async def echoue(c, m):
                return False

            self.assertEqual(await fiche_ephemere.balayer(db, echoue, maintenant=T0 + 25 * 3600), 0)
            self.assertEqual(await db.suppressions_dues(T0 + 25 * 3600), [("c1", "m1")])
            await db.close()

        _run(scenario())

    def test_message_sans_id_ignore(self):
        async def scenario():
            db = Database(self.chemin)
            await fiche_ephemere.programmer(db, None, maintenant=T0)
            await fiche_ephemere.programmer(db, {"channel_id": "c1"}, maintenant=T0)
            self.assertEqual(await db.suppressions_dues(T0 + 30 * 3600), [])
            await db.close()

        _run(scenario())

    def test_reponses_discord(self):
        def avec(statut, corps):
            async def appel():
                transport = httpx.MockTransport(lambda req: httpx.Response(statut, json=corps))
                async with httpx.AsyncClient(transport=transport) as client:
                    return await fiche_ephemere.supprimer_message(client, "jeton", "c1", "m1")
            return _run(appel())

        self.assertTrue(avec(204, None))
        self.assertTrue(avec(404, {"code": 10008, "message": "Unknown Message"}))
        self.assertTrue(avec(404, {"code": 10003, "message": "Unknown Channel"}))
        self.assertFalse(avec(429, {"retry_after": 1}))
        self.assertFalse(avec(500, {}))


if __name__ == "__main__":
    unittest.main()
