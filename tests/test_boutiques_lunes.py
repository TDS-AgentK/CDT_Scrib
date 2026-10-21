"""Lunes (copie du calcul du site), récompenses Rostheim « pleine lune », limite de quantité par boutique.
Lancer : python -m unittest discover -s tests
"""
import asyncio
import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import eco_vues, lunes, ros_boutique

REF = json.loads((Path(__file__).parent / "lunes_reference.json").read_text(encoding="utf-8"))


class MemeCalculQueLeSite(unittest.TestCase):
    def test_reference_du_site(self):
        # 2026 à 2028, produit par node scripts/lunes-reference.ts sur le site.
        self.assertGreater(len(REF["jours"]), 1000)
        for iso, jour, davos, amarante, pd, pa in REF["jours"]:
            d = date.fromisoformat(iso)
            self.assertEqual(lunes.jour_lunaire(d), jour, iso)
            self.assertEqual(lunes.etat(lunes.DAVOS, d)[1], davos, iso)
            self.assertEqual(lunes.etat(lunes.AMARANTE, d)[1], amarante, iso)
            self.assertAlmostEqual(lunes.etat(lunes.DAVOS, d)[0], pd, places=6)
            self.assertAlmostEqual(lunes.etat(lunes.AMARANTE, d)[0], pa, places=6)

    def test_jour_utc(self):
        # 23 h 30 à Paris le 29/10 = 22 h 30 UTC, même jour ; une heure sans fuseau est prise en UTC.
        self.assertEqual(lunes.jour_lunaire(datetime(2026, 10, 29, 22, 30, tzinfo=timezone.utc)), lunes.jour_lunaire(date(2026, 10, 29)))
        self.assertEqual(lunes.jour_lunaire(datetime(2026, 10, 29, 23, 59)), lunes.jour_lunaire(date(2026, 10, 29)))
        self.assertEqual(lunes.jour_lunaire(date(2028, 2, 29)), lunes.jour_lunaire(date(2028, 3, 1)))

    def test_prochaines_pleines_lunes(self):
        self.assertEqual(lunes.prochaines_pleines_lunes(lunes.DAVOS, date(2026, 10, 21), 2),
                         [(date(2026, 10, 29), date(2026, 10, 30)), (date(2026, 11, 11), date(2026, 11, 12))])
        self.assertEqual(lunes.prochaines_pleines_lunes(lunes.AMARANTE, date(2026, 10, 21), 1), [(date(2026, 10, 23), date(2026, 10, 27))])


class Disponibilite(unittest.TestCase):
    def test_disponible_et_mention(self):
        davos = {"disponibilite": "pleine_lune_davos"}
        amarante = {"disponibilite": "pleine_lune_amarante"}
        self.assertTrue(lunes.disponible({}, date(2026, 10, 21)))
        self.assertTrue(lunes.disponible({"disponibilite": "toujours"}, date(2026, 10, 21)))
        self.assertTrue(lunes.disponible({"disponibilite": ""}, date(2026, 10, 21)))
        self.assertFalse(lunes.disponible(davos, date(2026, 10, 21)))
        self.assertTrue(lunes.disponible(davos, date(2026, 10, 29)))
        self.assertTrue(lunes.disponible(amarante, date(2026, 10, 25)))
        self.assertFalse(lunes.disponible(amarante, date(2026, 10, 29)))
        self.assertEqual(lunes.mention(davos), "🌕 Pleine lune de Davos")
        self.assertEqual(lunes.mention(amarante), "🌕 Pleine lune d'Amarante")
        self.assertEqual(lunes.mention({"disponibilite": "toujours"}), "")


RECOMPENSES = [
    {"id": "r1", "libelle": "Gagner 10 XP", "prix": 5, "actif": True, "domaine": "d1", "disponibilite": "toujours"},
    {"id": "r2", "libelle": "Gagner 20 XP", "prix": 5, "actif": True, "domaine": "d1", "disponibilite": "pleine_lune_davos"},
    {"id": "r3", "libelle": "Gagner 30 XP", "prix": 5, "actif": True, "domaine": "d1", "disponibilite": "pleine_lune_amarante"},
]
DOMAINE = {"id": "d1", "nom": "Académie", "monnaie_nom": "Fragments"}


def faux_eco():
    async def lister(col, filtre="", tri=None, **k):
        return [dict(r) for r in RECOMPENSES] if col == "ros_recompenses" else [DOMAINE]

    async def premier(col, filtre):
        return next((dict(r) for r in RECOMPENSES if f'"{r["id"]}"' in filtre), None)
    return SimpleNamespace(pb=SimpleNamespace(lister=lister, premier=premier),
                           config=mock.AsyncMock(return_value={"reglages": {"rostheim_actif": True}, "boutiques": [], "articles": []}),
                           joueur_de=mock.AsyncMock(return_value=None), _boutiques_accessibles=lambda c, m: [])


class BoutiqueRostheim(unittest.TestCase):
    def libelles(self, jour):
        with mock.patch.object(lunes, "_jour_utc", lambda q: jour):
            return [r["libelle"] for r in asyncio.run(ros_boutique.articles(faux_eco(), DOMAINE))]

    def test_articles_masques_hors_pleine_lune(self):
        self.assertEqual(self.libelles(date(2026, 10, 21)), ["Gagner 10 XP"])
        self.assertEqual(self.libelles(date(2026, 10, 25)), ["Gagner 10 XP", "Gagner 30 XP"])
        self.assertEqual(self.libelles(date(2026, 10, 29)), ["Gagner 10 XP", "Gagner 20 XP"])

    def test_mention_dans_la_vue(self):
        with mock.patch.object(lunes, "_jour_utc", lambda q: date(2026, 10, 29)), \
                mock.patch.object(ros_boutique.eco_vues, "options_boutiques", mock.AsyncMock(return_value=[])):
            vue = asyncio.run(ros_boutique.vue(faux_eco(), SimpleNamespace(id=1), "r_d1"))
        textes = json.dumps(vue, ensure_ascii=False)
        self.assertIn("🌕 Pleine lune de Davos", textes)
        self.assertNotIn("Gagner 30 XP", textes)

    def test_achat_refuse_hors_pleine_lune(self):
        with mock.patch.object(lunes, "_jour_utc", lambda q: date(2026, 10, 21)):
            ok, txt = asyncio.run(ros_boutique.acheter(faux_eco(), SimpleNamespace(id=1), "d1", "r2"))
        self.assertFalse(ok)
        self.assertEqual(txt, "Cette récompense ne s'achète que pendant la pleine lune de Davos.")

    def test_achat_accepte_pendant_la_pleine_lune(self):
        # La vérification de lune passe : l'achat va plus loin (ici, faute de fiche joueur).
        with mock.patch.object(lunes, "_jour_utc", lambda q: date(2026, 10, 29)):
            ok, txt = asyncio.run(ros_boutique.acheter(faux_eco(), SimpleNamespace(id=1), "d1", "r2"))
        self.assertFalse(ok)
        self.assertEqual(txt, "Aucune fiche joueur liée à ce compte Discord.")


class QuantiteMax(unittest.TestCase):
    def test_quantite_max(self):
        illimite, stock3 = {"stock_illimite": True}, {"stock": 3}
        self.assertEqual(eco_vues.quantite_max({}, illimite), 25)
        self.assertEqual(eco_vues.quantite_max({"quantite_max": 0}, stock3), 3)
        self.assertEqual(eco_vues.quantite_max({"quantite_max": 1}, illimite), 1)
        self.assertEqual(eco_vues.quantite_max({"quantite_max": 10}, stock3), 3)

    def cfg(self, limite):
        return {"boutiques": [{"id": "b1", "quantite_max": limite}], "articles": [{"id": "a1", "nom": "Bonbon", "prix": 2, "stock_illimite": True}]}

    def test_pas_de_fenetre_si_un_seul_objet(self):
        eco = SimpleNamespace(config=mock.AsyncMock(return_value=self.cfg(1)), joueur_de=mock.AsyncMock(return_value=None))
        self.assertIsNone(asyncio.run(eco_vues.fenetre_quantite(eco, None, "b1", "a1")))

    def test_fenetre_ailleurs(self):
        eco = SimpleNamespace(config=mock.AsyncMock(return_value=self.cfg(0)), joueur_de=mock.AsyncMock(return_value=None))
        f = asyncio.run(eco_vues.fenetre_quantite(eco, None, "b1", "a1"))
        self.assertIn("entre 1 et 25", f["components"][0]["components"][0]["label"])

    def test_bouton_achete_directement_un_objet(self):
        from app import eco_interactions
        eco = SimpleNamespace(config=mock.AsyncMock(return_value=self.cfg(1)), joueur_de=mock.AsyncMock(return_value=None))
        lances = []
        with mock.patch.object(eco_interactions, "_en_fond", lambda t, fn, *a: lances.append((fn.__name__, a[-1]))):
            rep = asyncio.run(eco_interactions._composant(eco, {"data": {"custom_id": "eco:buy:b1:a1"}, "token": "t"}, None, [], "1"))
        self.assertEqual(rep["type"], eco_interactions.DIFFERE)
        self.assertEqual(lances, [("_achat", 1)])

    def test_achat_au_dela_de_la_limite_refuse(self):
        from app.economie import Economie
        eco = SimpleNamespace(config=mock.AsyncMock(return_value={"boutiques": []}), _boutiques_accessibles=lambda c, m: [{"id": "b1"}],
                              _verrou=lambda i: _Verrou(), joueur_de=mock.AsyncMock(return_value={"id": "j1", "eco_or": 100}),
                              pb=SimpleNamespace(requete=mock.AsyncMock(return_value={"id": "a1", "nom": "Bonbon", "prix": 2, "actif": True, "stock_illimite": True}),
                                                 maj=mock.AsyncMock()))
        ok, txt = asyncio.run(Economie.acheter(eco, SimpleNamespace(id=1), {"id": "b1", "quantite_max": 1}, {"id": "a1"}, "", 3))
        self.assertFalse(ok)
        self.assertEqual(txt, "Cette boutique ne permet d'acheter que 1 objet à la fois.")
        eco.pb.maj.assert_not_awaited()


class _Verrou:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class BoutiqueNormale(unittest.TestCase):
    ARTICLES = [{"id": "a1", "nom": "Bonbon", "prix": 2, "stock_illimite": True, "boutique": "b1", "actif": True, "disponibilite": ""},
                {"id": "a2", "nom": "Lanterne rouge", "prix": 5, "stock_illimite": True, "boutique": "b1", "actif": True, "disponibilite": "pleine_lune_davos"},
                {"id": "a3", "nom": "Voile d'argent", "prix": 5, "stock_illimite": True, "boutique": "b1", "actif": True, "disponibilite": "pleine_lune_amarante"}]

    def vue(self, jour):
        cfg = {"boutiques": [{"id": "b1", "nom": "Boutique du serveur"}], "articles": self.ARTICLES, "reglages": {}}
        eco = SimpleNamespace(config=mock.AsyncMock(return_value=cfg), _boutiques_accessibles=lambda c, m: cfg["boutiques"],
                              joueur_de=mock.AsyncMock(return_value=None))
        with mock.patch.object(lunes, "_jour_utc", lambda q: jour), mock.patch.object(eco_vues, "options_boutiques", mock.AsyncMock(return_value=[])):
            return json.dumps(asyncio.run(eco_vues.vue_boutique(eco, None, "b1")), ensure_ascii=False)

    def test_masque_hors_pleine_lune_et_mention(self):
        v = self.vue(date(2026, 10, 21))
        self.assertIn("Bonbon", v)
        self.assertNotIn("Lanterne rouge", v)
        self.assertNotIn("Voile d'argent", v)
        v = self.vue(date(2026, 10, 29))
        self.assertIn("Lanterne rouge", v)
        self.assertIn("🌕 Pleine lune de Davos", v)
        self.assertNotIn("Voile d'argent", v)
        self.assertIn("🌕 Pleine lune d'Amarante", self.vue(date(2026, 10, 25)))

    def test_achat_refuse_hors_pleine_lune(self):
        from app.economie import Economie
        eco = SimpleNamespace(config=mock.AsyncMock(return_value={"boutiques": []}), _boutiques_accessibles=lambda c, m: [{"id": "b1"}],
                              _verrou=lambda i: _Verrou(), joueur_de=mock.AsyncMock(return_value={"id": "j1", "eco_or": 100}),
                              pb=SimpleNamespace(requete=mock.AsyncMock(return_value=dict(self.ARTICLES[2])), maj=mock.AsyncMock()))
        with mock.patch.object(lunes, "_jour_utc", lambda q: date(2026, 10, 21)):
            ok, txt = asyncio.run(Economie.acheter(eco, SimpleNamespace(id=1), {"id": "b1"}, {"id": "a3"}, "", 1))
        self.assertFalse(ok)
        self.assertEqual(txt, "Cet article ne s'achète que pendant la pleine lune d'Amarante.")
        eco.pb.maj.assert_not_awaited()
