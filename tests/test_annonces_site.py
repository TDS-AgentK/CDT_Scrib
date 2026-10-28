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
        assert collection == "discord_annonces"
        if filtre == 'statut="en_attente"':
            return [dict(a) for a in self.annonces.values() if a.get("statut") == "en_attente"]
        if filtre == 'statut="a_modifier" || statut="a_supprimer"':
            return [dict(a) for a in self.annonces.values() if a.get("statut") in ("a_modifier", "a_supprimer")]
        # Effacement programmé : statut="envoye" && supprimer_le!="" && supprimer_le<="<date>"
        limite = filtre.split('supprimer_le<="')[1].rstrip('"')
        return [dict(a) for a in self.annonces.values()
                if a.get("statut") == "envoye" and a.get("supprimer_le") and a["supprimer_le"] <= limite]

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
            self.assertEqual(await annonces_site.tour(envoyer, pb, MAINTENANT), 1)
            self.assertEqual(await annonces_site.tour(envoyer, pb, MAINTENANT), 0)
        asyncio.run(scenario())
        self.assertEqual(len(postes), 1)

    def test_erreur_notee_sans_bloquer_la_file(self):
        pb, postes = FauxPb([_annonce("a1", salon=""), _annonce("a2", created="2026-10-12 14:59:30.000Z")]), []

        async def envoyer(salon, texte):
            if not salon:
                raise ValueError("salon non réglé")
            postes.append(salon)
            return ""

        asyncio.run(annonces_site.tour(envoyer, pb, MAINTENANT))
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

    def test_missive_effacee_apres_la_date(self):
        a = _annonce("m1")
        a["supprimer_le"] = "2026-10-15 15:00:00.000Z"
        pb, effaces = FauxPb([a, _annonce("m2")]), []

        async def envoyer(salon, texte):
            return "https://discord.com/channels/1/2/987654321"

        async def supprimer(salon, message):
            effaces.append((salon, message))

        async def scenario():
            await annonces_site.tour(envoyer, pb, MAINTENANT)
            self.assertEqual(pb.annonces["m1"]["message_id"], "987654321")
            self.assertNotIn("message_id", pb.annonces["m2"])  # sans effacement demandé
            self.assertEqual(await annonces_site.tour_suppressions(supprimer, pb, MAINTENANT), 0)  # trop tôt
            plus_tard = datetime(2026, 10, 15, 15, 0, 1, tzinfo=timezone.utc)
            self.assertEqual(await annonces_site.tour_suppressions(supprimer, pb, plus_tard), 1)
            self.assertEqual(await annonces_site.tour_suppressions(supprimer, pb, plus_tard), 0)
        asyncio.run(scenario())
        self.assertEqual(effaces, [("1285650689619525662", "987654321")])
        self.assertEqual(pb.annonces["m1"]["statut"], "supprime")
        self.assertEqual(pb.annonces["m2"]["statut"], "envoye")

    def test_effacement_rate_reessaye(self):
        a = _annonce("m1")
        a.update(statut="envoye", supprimer_le="2026-10-12 00:00:00.000Z", message_id="5")
        pb = FauxPb([a])

        async def supprimer(salon, message):
            raise RuntimeError("Discord indisponible")

        asyncio.run(annonces_site.tour_suppressions(supprimer, pb, MAINTENANT))
        self.assertEqual(pb.annonces["m1"]["statut"], "envoye")
        self.assertIn("Discord indisponible", pb.annonces["m1"]["erreur"])

    def test_missive_modifiee(self):
        a = _annonce("m1")
        a.update(statut="a_modifier", texte="Nouveau texte", lien="https://discord.com/channels/1/2/111")
        b = _annonce("m2")
        b.update(statut="a_supprimer", message_id="222")
        c = _annonce("m3")
        c.update(statut="a_modifier", texte="x", message_id="333")
        d = _annonce("m4")
        d.update(statut="a_modifier", texte="y", lien="")
        pb, modifies, effaces = FauxPb([a, b, c, d, _annonce("m5")]), [], []

        async def modifier(salon, message, texte):
            modifies.append((message, texte))
            return message != "333"  # m3 effacé à la main entre-temps

        async def supprimer(salon, message):
            effaces.append(message)

        self.assertEqual(asyncio.run(annonces_site.tour_modifications(modifier, supprimer, pb)), 4)
        self.assertEqual(modifies, [("111", "Nouveau texte"), ("333", "x")])
        self.assertEqual(effaces, ["222"])
        self.assertEqual(pb.annonces["m1"]["statut"], "envoye")
        self.assertEqual(pb.annonces["m1"]["message_id"], "111")
        self.assertEqual(pb.annonces["m2"]["statut"], "supprime")
        self.assertEqual(pb.annonces["m3"]["statut"], "supprime")
        self.assertEqual(pb.annonces["m4"]["statut"], "erreur")
        self.assertEqual(pb.annonces["m5"]["statut"], "en_attente")

    def test_modification_ratee_reessayee(self):
        a = _annonce("m1")
        a.update(statut="a_modifier", message_id="5")
        pb = FauxPb([a])

        async def modifier(salon, message, texte):
            raise RuntimeError("Discord indisponible")

        async def supprimer(salon, message):
            pass

        asyncio.run(annonces_site.tour_modifications(modifier, supprimer, pb))
        self.assertEqual(pb.annonces["m1"]["statut"], "a_modifier")
        self.assertIn("Discord indisponible", pb.annonces["m1"]["erreur"])


if __name__ == "__main__":
    unittest.main()
