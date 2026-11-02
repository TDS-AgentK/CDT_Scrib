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
    "s5": {"id": "s5", "title": "Meilleur qu'un blob", "description": "C'est le début du chemin", "members": ["u1"], "image": "/media/uploads/blob.png", "icon": "https://static.wixstatic.com/media/x.png"},
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
        self.assertEqual(e["icone"], "https://cdtsite-production.up.railway.app/media/uploads/blob.png")
        self.assertTrue(e["detenu"])

    def test_avatar_du_site(self):
        eco = mock.Mock()
        eco.pb.requete = mock.AsyncMock(return_value={"profile_photo": "https://lh3.example/photo.jpg"})
        self.assertEqual(asyncio.run(cr.avatar_site(eco, JOUEUR)), "https://lh3.example/photo.jpg")
        eco.pb.requete.assert_awaited_with("GET", "/api/collections/membres/records/u1")
        eco.pb.requete = mock.AsyncMock(return_value={})
        self.assertIsNone(asyncio.run(cr.avatar_site(eco, JOUEUR)))

    def test_prochaine_recompense(self):
        self.assertEqual(cr.prochaine_recompense(CFG, 12, SUCCES, JOUEUR), (15, "Rôle Slayers"))
        self.assertEqual(cr.prochaine_recompense(CFG, 15, SUCCES, JOUEUR), (20, "500 Or"))
        # récompense intermédiaire (niveau 13) avant l'étape suivante
        cfg = {**CFG, "recompenses": CFG["recompenses"] + [{"niveau": 13, "recompense_role": "Aventurier"},
                                                            {"niveau": 13, "recompense_or": 100}, {"niveau": 13, "recompense_objet": "Potion"}]}
        self.assertEqual(cr.prochaine_recompense(cfg, 12, SUCCES, JOUEUR), (13, "100 Or + Potion"))
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


class Classement(unittest.TestCase):
    def test_image_classement(self):
        from app import eco_vues
        joueurs = [{"id": f"j{i}", "pseudo": f"P{i}", "joueur": "u1" if i == 0 else "", "eco_xp": 1000 - i * 100} for i in range(7)]
        eco = mock.Mock()
        eco.pb.url = ""

        async def lister(col, filtre=None, tri=None, expand=None):
            return joueurs
        eco.pb.lister = lister
        eco.pb.requete = mock.AsyncMock(return_value={"profile_photo": ""})
        eco.config = mock.AsyncMock(return_value={**CFG, "reglages": {}, "courbe": {"formule": "personnalisee", "base_xp": 100, "pas_xp": 0, "courbure": 0, "arrondi": 1}})
        with mock.patch.object(cr, "_telecharger", mock.AsyncMock(return_value=None)), \
                mock.patch.object(cr, "succes_par_id", mock.AsyncMock(return_value=SUCCES)):
            embed, comp, image = asyncio.run(eco_vues.vue_classement_niveau(eco, 1))
        self.assertEqual(embed["image"]["url"], "attachment://carte.png")
        self.assertEqual(Image.open(io.BytesIO(image)).size[0], cr.LARGEUR)
        self.assertIn("Page 2/2", str(comp))
