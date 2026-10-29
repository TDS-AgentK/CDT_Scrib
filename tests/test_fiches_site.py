"""Fiches créées sur le site : embeds, envoi, modification d'un envoi, effacement 24 h, /fiche voir et pelage.
Lancer : python -m unittest discover -s tests
"""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from app import fiches_site
from app.database import Database
from app.embeds import build_character_embed
from app.handlers import handle_command

T0 = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)

FICHE = {
    "id": "f1", "nom_id": "amokualnesi", "couleur": 0x3BA55C, "nom": "Amokualnesi", "surnom": "Laideur",
    "genre": "♀", "race": "Hybride", "taille": "181 cm", "couleur_cheveux": "Noirs et blancs", "coiffure": "Une pince",
    "tenue": "Grande robe noire", "nom_skin": "Amo.png", "image_personnage_url": "https://x/portrait.png",
    "illustrations": [{"titre": "", "url": "https://x/tenue.png"}, {"titre": "Dague", "url": "https://x/dague.png"}],
}


def _run(coro):
    return asyncio.run(coro)


class FauxPB:
    def __init__(self, envois=(), fiches=()):
        self.envois = {e["id"]: dict(e) for e in envois}
        self.fiches = list(fiches)
        self.maj_faites = []

    async def maj(self, collection, id_, donnees):
        self.maj_faites.append((collection, id_, donnees))
        self.envois.get(id_, {}).update(donnees)
        return donnees

    async def lister(self, collection, filtre=None, tri=None, expand=None):
        if collection == fiches_site.COL_ENVOIS:
            return [dict(e, expand={"fiche": FICHE}) for e in self.envois.values()
                    if e["statut"] in ("en_attente", "a_modifier", "a_supprimer")
                    or (e["statut"] == "envoye" and e.get("expire_le", "") <= fiches_site._pb_date(T0 + timedelta(days=2)))]
        return [f for f in self.fiches if not filtre or f["nom_id"].startswith(filtre.split('"')[1].rstrip("%"))]

    async def premier(self, collection, filtre):
        nom = filtre.split('"')[1]
        return next((f for f in self.fiches if f["nom_id"] == nom), None)


class FauxDiscord:
    def __init__(self):
        self.messages = {}
        self.n = 0

    async def poster(self, salon, embeds):
        self.n += 1
        self.messages[str(self.n)] = embeds
        return str(self.n)

    async def editer(self, salon, message_id, embeds):
        if message_id not in self.messages:
            return False
        self.messages[message_id] = embeds
        return True

    async def effacer(self, salon, message_id):
        self.messages.pop(message_id, None)


class EmbedsTest(unittest.TestCase):
    def test_meme_mise_en_page_que_fiche_voir(self):
        principal, illustrations = fiches_site.messages_de(FICHE)
        self.assertEqual(principal["title"], "Amokualnesi")
        self.assertEqual(principal["description"], "Laideur")
        self.assertEqual(principal["thumbnail"]["url"], "https://x/portrait.png")
        self.assertEqual(principal["footer"]["text"], "Amokualnesi, Laideur")
        self.assertIn("Amo.png", principal["author"]["icon_url"])
        noms = [c["name"] for c in principal["fields"]]
        self.assertEqual(noms[:3], ["Sexe", "Race", "Taille"])
        self.assertIn("Coiffure", noms)
        self.assertEqual([e.get("title") for e in illustrations], ["Illustrations", "Dague"])
        self.assertTrue(all(e["color"] == 0x3BA55C for e in illustrations))

    def test_pelage_remplace_cheveux_et_coiffure(self):
        principal, _ = fiches_site.messages_de(dict(FICHE, couleur_pelage="Noir de jais"))
        noms = [c["name"] for c in principal["fields"]]
        self.assertIn("Couleur du pelage", noms)
        self.assertNotIn("Coiffure", noms)
        self.assertNotIn("Couleur et longueur des cheveux", noms)


class FileTest(unittest.TestCase):
    def test_envoi_modification_puis_effacement_a_24h(self):
        async def scenario():
            d = FauxDiscord()
            pb = FauxPB([{"id": "e1", "fiche": "f1", "salon_id": "123", "statut": "en_attente",
                          "created": fiches_site._pb_date(T0)}])
            await fiches_site.tour(pb, d.poster, d.editer, d.effacer, T0)
            e = pb.envois["e1"]
            self.assertEqual(e["statut"], "envoye")
            self.assertEqual(len(e["messages"]), 2)
            self.assertEqual(e["expire_le"], fiches_site._pb_date(T0 + timedelta(hours=24)))

            # Modification : mêmes messages, même heure d'effacement ; plus d'illustrations -> second message effacé.
            global FICHE
            ancienne = FICHE
            FICHE = dict(ancienne, surnom="La Belle", illustrations=[])
            e["statut"] = "a_modifier"
            await fiches_site.tour(pb, d.poster, d.editer, d.effacer, T0 + timedelta(hours=3))
            self.assertEqual(e["statut"], "envoye")
            self.assertEqual(e["expire_le"], fiches_site._pb_date(T0 + timedelta(hours=24)))
            self.assertEqual(d.messages[e["messages"][0]["id"]][0]["description"], "La Belle")
            self.assertEqual(len(d.messages), 1)
            FICHE = ancienne

            await fiches_site.tour(pb, d.poster, d.editer, d.effacer, T0 + timedelta(hours=23))
            self.assertEqual(e["statut"], "envoye")
            await fiches_site.tour(pb, d.poster, d.editer, d.effacer, T0 + timedelta(hours=24))
            self.assertEqual(e["statut"], "supprime")
            self.assertEqual(d.messages, {})

        _run(scenario())

    def test_salon_absent_note_une_erreur(self):
        async def scenario():
            d = FauxDiscord()
            pb = FauxPB([{"id": "e1", "fiche": "f1", "salon_id": "", "statut": "en_attente", "created": fiches_site._pb_date(T0)}])
            await fiches_site.tour(pb, d.poster, d.editer, d.effacer, T0)
            self.assertEqual(pb.envois["e1"]["statut"], "erreur")
            self.assertIn("salon", pb.envois["e1"]["erreur"])

        _run(scenario())


class CommandesTest(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.dossier.name, "fiches.db"))
        self.site = fiches_site.FichesSite(FauxPB(fiches=[FICHE]))

    def tearDown(self):
        _run(self.db.close())
        self.dossier.cleanup()

    def _cmd(self, sub, nom):
        data = {"options": [{"name": sub, "options": [{"name": "nom", "value": nom}]}]}
        return _run(handle_command(self.db, data, {"id": "42"}, self.site))

    def test_voir_une_fiche_du_site(self):
        reponse, suite = self._cmd("voir", "amokualnesi")
        self.assertEqual(reponse["data"]["embeds"][0]["title"], "Amokualnesi")
        self.assertEqual(len(suite), 2)

    def test_fiche_du_site_non_modifiable_sur_discord(self):
        reponse, _ = self._cmd("modifier", "amokualnesi")
        self.assertIn("créée sur le site", reponse["data"]["content"])
        reponse, _ = self._cmd("creer", "amokualnesi")
        self.assertIn("existe déjà", reponse["data"]["content"])

    def test_pelage_sur_une_fiche_du_bot(self):
        async def scenario():
            await self.db.create("loup", 42)
            await self.db.update_section("loup", {"nom": "Loup", "couleur_cheveux": "Gris", "couleur_pelage": "Gris cendre"})
            return build_character_embed(await self.db.get("loup"))
        noms = [c["name"] for c in _run(scenario())["fields"]]
        self.assertEqual(noms, ["Couleur du pelage"])


if __name__ == "__main__":
    unittest.main()
