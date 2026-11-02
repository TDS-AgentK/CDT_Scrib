"""Chemin des rangs : étapes (succès secrets cachés tant que non reçus), prochaine récompense, dessin des images.
Lancer : python -m unittest discover -s tests
"""
import asyncio
import io
import unittest
from unittest import mock

from PIL import Image

from app import chemin_rangs as cr

SUCCES = {
    "s5": {"id": "s5", "title": "Meilleur qu'un blob", "description": "C'est le début du chemin", "members": ["u1"], "icon": "blob.png"},
    "s15": {"id": "s15", "title": "Slayers", "members": []},
    "s25": {"id": "s25", "title": "Plafond de verre", "secret": True, "members": ["u1"]},
    "s27": {"id": "s27", "title": "Jamais vu auparavant", "secret": True, "members": []},
}
CFG = {"paliers": [{"declencheur": "niveau", "seuil": n, "succes": f"s{n}"} for n in (5, 15, 25, 27)]
                  + [{"declencheur": "or", "seuil": 1000, "succes": "autre"}],
       "recompenses": [{"niveau": 15, "libelle": "Rôle Slayers"}, {"niveau": 20, "recompense_or": 500},
                       {"niveau": 27, "categorie": "customisee", "succes": "s27"}]}
JOUEUR = {"id": "j1", "joueur": "u1"}


class Etapes(unittest.TestCase):
    def test_secret_non_recu_invisible(self):
        titres = [e["titre"] for e in cr.etapes(CFG, SUCCES, JOUEUR, "https://pb")]
        self.assertEqual(titres, ["Meilleur qu'un blob", "Slayers", "Plafond de verre"])

    def test_secret_invisible_pour_qui_ne_l_a_pas(self):
        titres = [e["titre"] for e in cr.etapes(CFG, SUCCES, {"id": "j2", "joueur": "u2"}, "https://pb")]
        self.assertEqual(titres, ["Meilleur qu'un blob", "Slayers"])

    def test_icone_pocketbase(self):
        e = cr.etapes(CFG, SUCCES, JOUEUR, "https://pb")[0]
        self.assertEqual(e["icone"], "https://pb/api/files/succes/s5/blob.png")
        self.assertTrue(e["detenu"])

    def test_prochaine_recompense(self):
        self.assertEqual(cr.prochaine_recompense(CFG, 12, SUCCES, JOUEUR), (15, "Rôle Slayers"))
        self.assertEqual(cr.prochaine_recompense(CFG, 15, SUCCES, JOUEUR), (20, "500 Or"))
        self.assertEqual(cr.prochaine_recompense(CFG, 26, SUCCES, JOUEUR), (27, "Récompense secrète"))
        self.assertIsNone(cr.prochaine_recompense(CFG, 30, SUCCES, JOUEUR))

    def test_fenetre(self):
        etapes = [{"niveau": n} for n in (5, 10, 15, 20, 25, 30, 35)]
        vue, depart = cr.fenetre(etapes, 22)
        self.assertEqual(([e["niveau"] for e in vue], depart), ([15, 20, 25, 30, 35], 10))
        vue, depart = cr.fenetre(etapes, 2)
        self.assertEqual(([e["niveau"] for e in vue], depart), ([5, 10, 15, 20, 25], 0))

    def test_phrases(self):
        self.assertIn("succès secret", cr.phrase("@M", 24, 25, {"titre": "Plafond de verre", "secret": True}))
        self.assertIn("**Slayers**", cr.phrase("@M", 14, 15, {"titre": "Slayers"}))
        self.assertIn("3 niveaux", cr.phrase("@M", 5, 8, None))


class Dessin(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(cr, "_telecharger", mock.AsyncMock(return_value=None))
        p.start()
        self.addCleanup(p.stop)

    def test_carte(self):
        etapes = cr.etapes(CFG, SUCCES, JOUEUR, "")
        png = asyncio.run(cr.carte("Mirelle", None, 12, 4380, 5000, 4, "Meilleur qu'un blob", etapes, (15, "Rôle Slayers")))
        self.assertEqual(Image.open(io.BytesIO(png)).size, (cr.LARGEUR, cr.HAUTEUR))

    def test_carte_sans_etape(self):
        png = asyncio.run(cr.carte("Gaspard", None, 0, 0, 100, 0, "", [], None))
        self.assertEqual(Image.open(io.BytesIO(png)).size, (cr.LARGEUR, cr.HAUTEUR))

    def test_bannieres(self):
        etapes = cr.etapes(CFG, SUCCES, JOUEUR, "")
        for etape in (None, etapes[0], etapes[-1]):
            png = asyncio.run(cr.banniere("Mirelle", None, 4, 5, etapes, etape, ["Rôle"]))
            self.assertEqual(Image.open(io.BytesIO(png)).size, (cr.LARGEUR, cr.BANNIERE_H))


if __name__ == "__main__":
    unittest.main()
