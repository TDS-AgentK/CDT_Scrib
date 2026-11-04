"""Trésor : carte /argent et classement /topargent en image (avec repli texte).
Lancer : python -m unittest discover -s tests
"""
import asyncio
import io
import unittest
from unittest import mock

from PIL import Image

from app import chemin_rangs as cr
from app import tresor

JOUEURS = [{"id": f"j{i}", "pseudo": f"P{i}", "pseudo_discord": f"p{i}", "joueur": "", "eco_or": 5000 - i * 600,
            "eco_or_record": 6000, "eco_or_depense": 120 * i} for i in range(7)]

ACHATS = [{"prix": 300}, {"prix": 1200}]
MOUVEMENTS = [{"quantite": 2, "prix_unitaire": 150}, {"quantite": 1, "prix_unitaire": 40}]


def _eco(emoji_id=""):
    eco = mock.Mock()
    eco.pb.url = ""

    async def lister(col, filtre=None, tri=None, expand=None):
        eco.filtres.append((col, filtre))
        return {"joueurs": JOUEURS, "eco_achats": ACHATS, "rec_mouvements": MOUVEMENTS}.get(col, [])
    eco.filtres = []
    eco.pb.lister = lister
    eco.pb.requete = mock.AsyncMock(return_value={"profile_photo": ""})
    eco.config = mock.AsyncMock(return_value={"reglages": {"or_emoji_id": emoji_id}})
    eco.joueur_de = mock.AsyncMock(return_value=JOUEURS[2])
    return eco


class Dessin(unittest.TestCase):
    def test_carte_et_classement(self):
        with mock.patch.object(tresor, "_telecharger", mock.AsyncMock(return_value=None)):
            img = asyncio.run(tresor.carte("Kelora", None, 12450, 25300, 48210, 3120, 3))
            self.assertEqual(Image.open(io.BytesIO(img)).size, (cr.LARGEUR, cr.HAUTEUR))
            img = asyncio.run(tresor.carte("Vide", None, 0, 0, 0, 0, 0))
            self.assertEqual(Image.open(io.BytesIO(img)).size, (cr.LARGEUR, cr.HAUTEUR))
            lignes = [{"rang": i + 1, "nom": f"P{i}", "solde": 10 ** (7 - i), "record": 0} for i in range(5)]
            img = asyncio.run(tresor.classement(lignes, 0, 1))
            self.assertEqual(Image.open(io.BytesIO(img)).size,
                             (cr.LARGEUR, tresor.CLASSEMENT_ENTETE + 5 * tresor.CLASSEMENT_LIGNE + tresor.CLASSEMENT_PIED))
            asyncio.run(tresor.classement([], 0, 1))

    def test_piece_avec_emoji(self):
        emoji = Image.new("RGBA", (64, 32), (255, 0, 0, 255))
        p = tresor.piece(40, emoji)
        self.assertEqual(p.size, (40, 40))
        self.assertEqual(p.getpixel((20, 20))[:3], (255, 0, 0))


class Vues(unittest.TestCase):
    def setUp(self):
        tresor._emoji.clear()

    def test_carte_argent(self):
        from app import eco_vues
        cible = mock.Mock(display_name="Kelora", display_avatar=mock.Mock(url=""))
        with mock.patch.object(cr, "_telecharger", mock.AsyncMock(return_value=None)), \
                mock.patch.object(tresor, "_telecharger", mock.AsyncMock(return_value=None)):
            embed, image = asyncio.run(eco_vues.carte_argent(_eco(), cible))
        self.assertEqual(embed["image"]["url"], "attachment://carte.png")
        self.assertEqual(Image.open(io.BytesIO(image)).size, (cr.LARGEUR, cr.HAUTEUR))

    def test_depenses_boutique_et_receleur_separees(self):
        from app import eco_vues
        eco = _eco()
        self.assertEqual(asyncio.run(eco_vues.depenses(eco, "j2")), (1500, 340))
        self.assertIn(("eco_achats", 'joueur="j2"'), eco.filtres)
        self.assertIn(("rec_mouvements", 'joueur="j2" && sens="vente"'), eco.filtres)

    def test_carte_argent_transmet_les_deux_depenses(self):
        from app import eco_vues
        cible = mock.Mock(display_name="Kelora", display_avatar=mock.Mock(url=""))
        carte = mock.AsyncMock(return_value=b"png")
        with mock.patch.object(cr, "_telecharger", mock.AsyncMock(return_value=None)), mock.patch.object(tresor, "carte", carte):
            asyncio.run(eco_vues.carte_argent(_eco(), cible))
        self.assertEqual(carte.call_args.args[4:6], (1500, 340))

    def test_classement_argent_page_2(self):
        from app import eco_vues
        telecharger = mock.AsyncMock(return_value=None)
        with mock.patch.object(cr, "_telecharger", telecharger), mock.patch.object(tresor, "_telecharger", telecharger):
            embed, comp, image = asyncio.run(eco_vues.vue_classement_argent(_eco("123"), 1))
        self.assertEqual(embed["image"]["url"], "attachment://carte.png")
        self.assertIn("Page 2/2", str(comp))
        self.assertIn("eco:top:argent:0", str(comp))
        self.assertIn("https://cdn.discordapp.com/emojis/123.png?size=128", str(telecharger.call_args_list))

    def test_repli_texte_si_dessin_impossible(self):
        from app import eco_vues
        with mock.patch.object(tresor, "classement", mock.AsyncMock(side_effect=RuntimeError("boum"))), \
                mock.patch.object(cr, "_telecharger", mock.AsyncMock(return_value=None)), \
                mock.patch.object(tresor, "_telecharger", mock.AsyncMock(return_value=None)), \
                self.assertLogs("cdt_scrib", "ERROR"):
            embed, comp, image = asyncio.run(eco_vues.vue_classement_argent(_eco(), 0))
        self.assertIsNone(image)
        self.assertIn("P0", embed["description"])


class ReceleurSuccesDepense(unittest.TestCase):
    def test_seuil_franchi_au_receleur_donne_le_succes(self):
        from app import receleur
        eco = mock.Mock()
        eco._verrou = lambda _id: asyncio.Lock()
        eco.joueur_de = mock.AsyncMock(return_value={"id": "j1"})
        eco.config = mock.AsyncMock(return_value={"reglages": {}, "paliers": [
            {"declencheur": "or_depense", "seuil": 1000, "succes": "sD"},
            {"declencheur": "or_depense", "seuil": 5000, "succes": "sX"}]})
        eco.pb.requete = mock.AsyncMock(return_value={"eco_or": 9999})
        eco.pb.maj = mock.AsyncMock()
        eco.ajouter_objet = mock.AsyncMock()
        eco.donner_succes = mock.AsyncMock()
        apres = {"id": "j1", "joueur": "u1", "eco_or": 9000, "eco_or_depense": 900}
        with mock.patch.object(receleur, "reglages", mock.AsyncMock(return_value={})), \
                mock.patch.object(receleur, "_ferme", return_value=None), \
                mock.patch.object(receleur, "objet_par_nom", mock.AsyncMock(return_value={"id": "o1", "nom": "Dague"})), \
                mock.patch.object(receleur, "_ligne", mock.AsyncMock(return_value={"id": "l1", "prix_vente": 100, "stock": 5})), \
                mock.patch.object(receleur, "changer_or", mock.AsyncMock(return_value=apres)), \
                mock.patch.object(receleur, "_mouvement", mock.AsyncMock()) as mouvement, \
                mock.patch.object(receleur, "_gain", mock.AsyncMock()):
            ok, _ = asyncio.run(receleur.acheter(eco, mock.Mock(id=1), "o1", 2, "test"))
        self.assertTrue(ok)
        eco.pb.maj.assert_any_await("joueurs", "j1", {"eco_or_depense": 1100})
        self.assertEqual(mouvement.await_args.args[2:6], ("o1", 2, 100, "vente"))
        eco.donner_succes.assert_awaited_once_with(apres, "sD")


if __name__ == "__main__":
    unittest.main()
