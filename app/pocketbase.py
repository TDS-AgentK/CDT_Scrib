"""Accès à la base PocketBase du site des Chroniques du Temps (économie, joueurs, inventaires).

Le bot s'authentifie comme superuser (compte dédié conseillé) : les collections eco_* n'ont aucune règle
publique. Identifiants uniquement par variables d'environnement (PB_URL, PB_EMAIL, PB_PASSWORD).
"""
import logging
import time

import httpx

log = logging.getLogger("cdt_scrib.pocketbase")


def echapper(valeur) -> str:
    """Valeur insérable entre guillemets dans un filtre PocketBase."""
    return str(valeur).replace("\\", "\\\\").replace('"', '\\"')


class PocketBase:
    def __init__(self, url: str, email: str, password: str):
        self.url = url.rstrip("/")
        self.email = email
        self.password = password
        self._jeton = None
        self._expire = 0.0
        self._client = httpx.AsyncClient(timeout=15)

    async def close(self):
        await self._client.aclose()

    async def _auth(self) -> str:
        if self._jeton and time.time() < self._expire:
            return self._jeton
        resp = await self._client.post(
            f"{self.url}/api/collections/_superusers/auth-with-password",
            json={"identity": self.email, "password": self.password},
        )
        resp.raise_for_status()
        self._jeton = resp.json()["token"]
        self._expire = time.time() + 15 * 60  # rafraîchi bien avant l'expiration réelle
        return self._jeton

    async def requete(self, methode: str, chemin: str, json=None, params=None):
        resp = await self._client.request(
            methode, f"{self.url}{chemin}", json=json, params=params,
            headers={"Authorization": await self._auth()},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"PocketBase {methode} {chemin} -> {resp.status_code} {resp.text}")
        return None if resp.status_code == 204 else resp.json()

    async def lister(self, collection: str, filtre: str | None = None, tri: str | None = None, expand: str | None = None) -> list[dict]:
        sortie, page = [], 1
        while True:
            params = {"page": page, "perPage": 200, "skipTotal": 1}
            if filtre:
                params["filter"] = filtre
            if tri:
                params["sort"] = tri
            if expand:
                params["expand"] = expand
            items = (await self.requete("GET", f"/api/collections/{collection}/records", params=params))["items"]
            sortie.extend(items)
            if len(items) < 200:
                return sortie
            page += 1

    async def premier(self, collection: str, filtre: str) -> dict | None:
        params = {"page": 1, "perPage": 1, "skipTotal": 1, "filter": filtre}
        items = (await self.requete("GET", f"/api/collections/{collection}/records", params=params))["items"]
        return items[0] if items else None

    async def creer(self, collection: str, donnees: dict) -> dict:
        return await self.requete("POST", f"/api/collections/{collection}/records", json=donnees)

    async def maj(self, collection: str, id_: str, donnees: dict) -> dict:
        return await self.requete("PATCH", f"/api/collections/{collection}/records/{id_}", json=donnees)

    async def supprimer(self, collection: str, id_: str):
        return await self.requete("DELETE", f"/api/collections/{collection}/records/{id_}")
