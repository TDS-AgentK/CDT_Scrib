"""Tests des annonces demandées par le site (file discord_annonces, ex. « Nouvelle fiche sur le site … »).
Lancer : python -m unittest discover -s tests
"""
import asyncio
import unittest
from datetime import datetime, timezone

from app import annonces_site

MAINTENANT = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)


class FauxPb:
    def __init__(self, annonces):
        self.annonces = {a["id"]: dict(a) for a in annonces}
        self.majs = []

    async def lister(self, collection, filtre=None, tri=None, expand=None):
        assert collection == "discord_annonces" and filtre == 'statut="en_attente"'
        return [dict(a) for a in self.annonces.values() if a.get("statut") == "en_attente"]

    async def maj(self, collection, id_, donnees):
        self.majs.append((collection, id_, donnees))
        self.annonces[id_].update(donnees)
        return self.annonces[id_]


def _annonce(id_, created="2026-10-12 14:59:00.000Z", salon="1285650689619525662"):
    return {"id": id_, "salon_id": salon, "statut": "en_attente", "created": created,
            "texte": "Nouvelle fiche sur le site [Gaynor](<https://cdtsite-production.up.railway.app/fiche/gaynor>)."}


class AnnoncesSiteTest(unittest.TestCase):
    def test_poste_le_texte_puis_note_le_lien(self):
        pb, postes = FauxPb([_annonce("a1")]), []

        async def envoyer(salon, texte):
            postes.append((salon, texte))
            return "https://discord.com/channels/1/2/3"

        maj = asyncio.run(annonces_site.traiter(envoyer, pb, pb.annonces["a1"], MAINTENANT))
        self.assertEqual(postes, [("1285650689619525662", "Nouvelle fiche sur le site [Gaynor](<https://cdtsite-production.up.railway.app/fiche/gaynor>).")])
        self.assertEqual(maj["statut"], "envoye")
        self.assertEqual(pb.annonces["a1"]["lien"], "https://discord.com/channels/1/2/3")

    def test_une_seule_fois(self):
        pb, postes = FauxPb([_annonce("a1")]), []

        async def envoyer(salon, texte):
            postes.append(texte)
            return ""

        async def scenario():
            self.assertEqual(await annonces_site.tour(envoyer, pb), 1)
            self.assertEqual(await annonces_site.tour(envoyer, pb), 0)
        asyncio.run(scenario())
        self.assertEqual(len(postes), 1)

    def test_erreur_notee_sans_bloquer_la_file(self):
        pb, postes = FauxPb([_annonce("a1", salon=""), _annonce("a2", created="2026-10-12 14:59:30.000Z")]), []

        async def envoyer(salon, texte):
            if not salon:
                raise ValueError("salon non réglé")
            postes.append(salon)
            return ""

        asyncio.run(annonces_site.tour(envoyer, pb))
        self.assertEqual(pb.annonces["a1"]["statut"], "erreur")
        self.assertIn("salon non réglé", pb.annonces["a1"]["erreur"])
        self.assertEqual(pb.annonces["a2"]["statut"], "envoye")

    def test_trop_ancienne_non_postee(self):
        pb, postes = FauxPb([_annonce("a1", created="2026-10-10 10:00:00.000Z")]), []

        async def envoyer(salon, texte):
            postes.append(texte)
            return ""

        asyncio.run(annonces_site.traiter(envoyer, pb, pb.annonces["a1"], MAINTENANT))
        self.assertEqual(postes, [])
        self.assertEqual(pb.annonces["a1"]["statut"], "erreur")


if __name__ == "__main__":
    unittest.main()
