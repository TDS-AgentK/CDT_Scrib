"""Connexion admin à PocketBase : un seul login même si plusieurs tâches demandent le jeton en même temps.
Lancer : python -m unittest tests.test_pocketbase_auth
"""
import asyncio
import unittest

import httpx

from app.pocketbase import PocketBase


class UnSeulLogin(unittest.TestCase):
    def test_logins_simultanes(self):
        appels = []

        async def scenario():
            async def repondre(requete: httpx.Request) -> httpx.Response:
                appels.append(requete.url.path)
                await asyncio.sleep(0.05)  # le temps que les autres tâches arrivent
                return httpx.Response(200, json={"token": "jeton"})

            pb = PocketBase("https://pb.test", "bot@test", "secret")
            await pb._client.aclose()
            pb._client = httpx.AsyncClient(transport=httpx.MockTransport(repondre))
            jetons = await asyncio.gather(*(pb._auth() for _ in range(15)))
            await pb.close()
            return jetons

        jetons = asyncio.run(scenario())
        self.assertEqual(jetons, ["jeton"] * 15)
        self.assertEqual(appels, ["/api/collections/_superusers/auth-with-password"])


if __name__ == "__main__":
    unittest.main()
