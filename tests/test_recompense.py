"""Tests de /recompense : gains et outils (--wut) passent par la commande slash, plus aucun déclenchement par texte.
Lancer : python -m unittest discover -s tests
"""
import asyncio
import unittest
from types import SimpleNamespace

import discord

from app.economie import Economie
from app.rostheim import TYPES_RECOMPENSE, Rostheim

PLACE = {"id": "dplace", "nom": "Place publique", "ordre": 5, "salon_nom": "place-publique", "monnaie_nom": "Marque"}
ACAD = {"id": "dacad", "nom": "Académie", "ordre": 1, "salon_nom": "académie", "monnaie_nom": "Fragments de savoir"}
WUT = {"id": "cwut", "commande": "--wut", "domaine": "dplace", "type": "outil", "actif": True, "role_requis": "Fou du roi",
       "salon_autorise": "place-publique", "reponses": "Un chat\n\nUn chapeau\n"}
VIDE = {"id": "cvide", "commande": "--rien", "domaine": "dplace", "type": "outil", "actif": True, "reponses": ""}
TEXTE = {"id": "ctexte", "commande": "??texte", "domaine": "dacad", "type": "gain", "actif": True, "gain_monnaie": 15, "points_jauge": 15}
BOUTIQUE = {"id": "cbout", "commande": "??dépenser-marque", "domaine": "dplace", "type": "boutique", "actif": True}


class FauxPb:
    commandes = [WUT, VIDE, TEXTE, BOUTIQUE]

    async def lister(self, collection, filtre=None, tri=None):
        return {"ros_commandes": self.commandes, "ros_domaines": [ACAD, PLACE]}.get(collection, [])

    async def premier(self, collection, filtre=None):
        return None


class FauxEco:
    prefixe = "!!"

    def __init__(self, salons=None):
        self.pb = FauxPb()
        self.salons = salons or {}
        self.client = SimpleNamespace(get_channel=lambda i: self.salons.get(i), fetch_channel=self._fetch)

    async def _fetch(self, i):
        raise discord.HTTPException(SimpleNamespace(status=404, reason="Not Found"), "salon inconnu")

    async def config(self):
        return {"reglages": {"rostheim_actif": True}}


def membre(*roles):
    return SimpleNamespace(roles=[SimpleNamespace(name=r) for r in roles], guild=None, mention="@joueur")


class Message:
    def __init__(self, contenu, auteur, salon):
        self.content, self.author, self.channel = contenu, auteur, salon
        self.reponses, self.envoyes = [], []
        salon.send = self._envoyer

    async def reply(self, texte, **_):
        self.reponses.append(texte)

    async def _envoyer(self, texte):
        self.envoyes.append(texte)


def lancer(coro):
    return asyncio.run(coro)


class Recompense(unittest.TestCase):
    def setUp(self):
        self.place = SimpleNamespace(id=1, name="📢・place-publique")
        self.ailleurs = SimpleNamespace(id=2, name="général")
        self.r = Rostheim(FauxEco({1: self.place, 2: self.ailleurs}))

    def test_types_de_recompense(self):
        self.assertEqual(Economie.rostheim_types("recompense"), TYPES_RECOMPENSE)
        self.assertIn("outil", TYPES_RECOMPENSE)

    def test_liste_proposee(self):
        noms = [c["commande"] for _d, c in lancer(self.r.actions(TYPES_RECOMPENSE))]
        self.assertEqual(sorted(noms), ["--wut", "??texte"])  # ni boutique, ni outil sans réponse
        self.assertEqual(self.r.nom_court(WUT), "Wut")

    def test_wut_par_slash(self):
        ok, texte = lancer(self.r.slash(membre("Fou du roi"), "cwut", TYPES_RECOMPENSE, "1", "lien"))
        self.assertTrue(ok)
        self.assertIn(texte, ("Un chat", "Un chapeau"))

    def test_wut_sans_le_role(self):
        ok, texte = lancer(self.r.slash(membre("Sage"), "cwut", TYPES_RECOMPENSE, "1", "lien"))
        self.assertFalse(ok)
        self.assertIn("Fou du roi", texte)

    def test_wut_hors_du_salon(self):
        ok, texte = lancer(self.r.slash(membre("Fou du roi"), "cwut", TYPES_RECOMPENSE, "2", "lien"))
        self.assertFalse(ok)
        self.assertIn("place-publique", texte)

    def test_wut_salon_invisible(self):
        ok, texte = lancer(self.r.slash(membre("Fou du roi"), "cwut", TYPES_RECOMPENSE, "99", "lien"))
        self.assertFalse(ok)  # plus de plantage quand le bot ne voit pas le salon
        self.assertIn("place-publique", texte)

    def test_wut_tape_ne_fait_que_rediriger(self):
        m = Message("!!wut", membre("Fou du roi"), self.place)  # « -- » suit le préfixe du bot (!! en ligne)
        self.assertTrue(lancer(self.r.traiter(m)))
        self.assertEqual(m.envoyes, [])
        self.assertIn("/recompense", m.reponses[0])
        self.assertIn("Place publique", m.reponses[0])

    def test_gain_tape_ne_fait_que_rediriger(self):
        m = Message("!!texte", membre("Érudit"), self.place)
        self.assertTrue(lancer(self.r.traiter(m)))
        self.assertIn("/recompense", m.reponses[0])

    def test_commande_supprimee_tapee(self):
        # ??dépenser-marque supprimée de la base : le message n'est plus une commande Rostheim, rien ne plante.
        self.r.pb.commandes = [WUT, TEXTE]
        m = Message("!!dépenser-marque", membre("Fou du roi"), self.place)
        self.assertFalse(lancer(self.r.traiter(m)))
        self.assertEqual((m.reponses, m.envoyes), ([], []))


if __name__ == "__main__":
    unittest.main()
