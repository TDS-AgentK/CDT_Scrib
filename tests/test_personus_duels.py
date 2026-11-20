"""Duels de Personus sur Discord (app/personus_duels.py).
Lancer : python -m unittest tests.test_personus_duels
"""
import asyncio
import unittest
from unittest import mock

from app import personus_duels as pd


class Regles(unittest.TestCase):
    def test_bornes_comme_le_site(self):
        # Mêmes cas que tests/personus-duel.test.ts du site : de mise + 50 à la plus petite bourse, par pas de 50.
        self.assertEqual(pd.bornes_surenchere(100, 1000, 380), (150, 350))
        self.assertEqual(pd.bornes_surenchere(100, 150, 9000), (150, 150))
        self.assertIsNone(pd.bornes_surenchere(100, 149, 9000))

    def test_montant_tape(self):
        b = (150, 2250)
        self.assertEqual(pd.montant_valide("300", b, 100), 300)
        self.assertEqual(pd.montant_valide(" 1 000 PO", b, 100), 1000)
        self.assertIsNone(pd.montant_valide("325", b, 100))   # pas de 50
        self.assertIsNone(pd.montant_valide("100", b, 100))   # sous le minimum
        self.assertIsNone(pd.montant_valide("3000", b, 100))  # au-dessus de la plus petite bourse
        self.assertIsNone(pd.montant_valide("deux cents", b, 100))

    def test_fenetre_surenchere(self):
        f = pd.fenetre_surenchere("d1", (300, 2900), 250)
        champ = f["components"][0]["components"][0]
        self.assertEqual(f["custom_id"], "eco:pdm:d1")
        self.assertEqual(champ["custom_id"], "montant")
        self.assertEqual(champ["value"], "300")
        self.assertIn("300 à 2900", champ["label"])
        self.assertLessEqual(len(champ["label"]), 45)


class Carte(unittest.TestCase):
    def test_boutons_de_l_etape(self):
        a = {"boutons": ["accepter", "surencherir", "retirer", "inconnu"]}
        self.assertEqual(pd.codes_boutons(a), ["acc", "sur", "ret"])
        c = pd.carte("d1", "surenchere", "Fyb relève la mise à 250 PO !", ["acc", "sur", "ret"], ["111", "", "222"])
        self.assertEqual(c["content"], "<@111> <@222>")
        ids = [b.get("custom_id") for b in c["components"][0]["components"]]
        self.assertEqual(ids, ["eco:pdu:d1:acc", "eco:pdu:d1:sur", "eco:pdu:d1:ret"])

    def test_duel_accepte_lien_vers_le_site(self):
        c = pd.carte("d1", "accepte", "Les mises sont posées.", [], [])
        b = c["components"][0]["components"][0]
        self.assertEqual(b["style"], 5)
        self.assertTrue(b["url"].endswith("/personus?onglet=duels&duel=d1"))

    def test_fin_sans_boutons(self):
        c = pd.carte("d1", "resultat", "Kelora gagne le duel.", [], ["1"])
        self.assertEqual(c["components"], [])
        self.assertNotIn("footer", c["embeds"][0])

    def test_recapitulatif_a_confirmer(self):
        c = pd.carte_a_confirmer("d1", 111, 222)
        self.assertIn("<@111>", c["embeds"][0]["description"])
        self.assertIn("<@222>", c["embeds"][0]["description"])
        self.assertEqual([b["custom_id"] for b in c["components"][0]["components"]], ["eco:pdu:d1:conf", "eco:pdu:d1:ret"])
        self.assertIn("défi ouvert", pd.carte_a_confirmer("d1", 111, None)["embeds"][0]["description"])


class FausseBase:
    """Base minimale : la demande déposée est « traitée » par le faux site au premier relu."""
    def __init__(self, reponse):
        self.reponse, self.cree = reponse, None

    async def creer(self, collection, donnees):
        self.cree = (collection, donnees)
        return {"id": "dem1"}

    async def requete(self, methode, chemin, **_):
        return self.reponse


class Deposer(unittest.TestCase):
    def setUp(self):
        self.reveil = mock.patch.object(pd, "_reveiller", mock.AsyncMock())
        self.reveil.start()

    def tearDown(self):
        self.reveil.stop()

    def test_traitee_renvoie_le_duel(self):
        pb = FausseBase({"statut": "traitee", "duel": "d9"})
        self.assertEqual(asyncio.run(pd.deposer(pb, "creer", 111, ouvert=True, salon_id="5")), "d9")
        self.assertEqual(pb.cree[0], "personus_duel_demandes")
        self.assertEqual(pb.cree[1], {"action": "creer", "discord_id": "111", "statut": "a_traiter", "ouvert": True, "salon_id": "5"})

    def test_erreur_du_site(self):
        pb = FausseBase({"statut": "erreur", "erreur": "Ce n'est pas à vous de répondre : l'autre joueur a la main."})
        with self.assertRaisesRegex(RuntimeError, "pas à vous"):
            asyncio.run(pd.deposer(pb, "accepter", 111, duel="d1"))

    def test_site_muet(self):
        pb = FausseBase({"statut": "a_traiter"})
        with mock.patch.object(pd.asyncio, "sleep", mock.AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, "pas encore traité"):
                asyncio.run(pd.deposer(pb, "accepter", 111, secondes=1, duel="d1"))


if __name__ == "__main__":
    unittest.main()
