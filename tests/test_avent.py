"""Tests du calendrier de l'Avent (app/avent.py) : ouverture du jour, doublon, fenêtre future, rattrapage, bonus des 24,
fragments du Conte, annonce de minuit et rappel du soir (une seule fois par jour).
Lancer : python -m unittest discover -s tests
"""
import asyncio
import re
import unittest
from datetime import datetime, timezone
from unittest import mock

from app import avent
from tests.test_halloween import FauxEco, FauxPb, membre


class Pb(FauxPb):
    """Ajoute les filtres numériques (jour=3) et l'index unique (joueur, jour) d'avent_ouvertures."""

    @staticmethod
    def _ok(rec, filtre):
        if not FauxPb._ok(rec, filtre):
            return False
        for champ, val in re.findall(r'(\w+)=(\d+)(?!")', filtre or ""):
            if str(rec.get(champ, "")) != val:
                return False
        return True

    async def lister(self, collection, filtre=None, tri=None, expand=None):
        return [r for r in self.d.get(collection, []) if self._ok(r, filtre)]

    async def creer(self, collection, champs):
        if collection == "avent_ouvertures" and any(
                o["joueur"] == champs["joueur"] and o["jour"] == champs["jour"] for o in self.d.get(collection, [])):
            raise RuntimeError("PocketBase POST -> 400 unique")
        return await super().creer(collection, champs)


class Eco(FauxEco):
    _charge_a = 1


REGLAGES = {"id": "reg", "cle": "general", "annee": 2026, "ouvert": True, "heure_ouverture": "00:00",
            "rattrapage_jusqu_au": "12-25", "bonus_compte_rattrapees": False, "salon_annonce": "111", "rappel": "salon",
            "salon_rappel": "222", "heure_rappel": "20:00",
            "bonus": [{"type": "role_temporaire", "role_nom": "Citrouille", "duree_jours": 5}],
            "derniere_annonce": "", "dernier_rappel": ""}


def base(ouvertures=()):
    cases = [{"id": f"c{j}", "jour": j, "nom": f"Case {j}", "phrase": "De l'or, merci.", "fragment": 0,
              "recompenses": [{"type": "objet", "objet": "o1", "objet_nom": "Masque", "quantite": 1}]} for j in range(1, 26)]
    cases[1] = {**cases[1], "nom": "Le Conte de Lanjis, fragment I", "recompenses": [], "fragment": 1}
    return {
        "avent_reglages": [dict(REGLAGES)], "avent_cases": cases,
        "avent_fragments": [{"id": "f1", "ordre": 5, "jour": 2, "texte": "Il neigeait sur Rostheim."}],
        "avent_ouvertures": [dict(o) for o in ouvertures],
        "joueurs": [{"id": "j1", "pseudo": "Ràlas", "discord_id": "123456789012345678", "eco_or": 0}],
        "eco_inventaire": [], "eco_objets": [{"id": "o1", "nom": "Masque"}], "eco_gains": [],
    }


def le(jour, heure=12, mois=12):
    return datetime(2026, mois, jour, heure, 0, tzinfo=avent.PARIS).astimezone(timezone.utc)


def run(coro):
    avent.oublier_cache()
    return asyncio.run(coro)


def ouverture(jour, rattrapee=False):
    return {"id": f"o{jour}", "joueur": "j1", "jour": jour, "rattrapee": rattrapee, "source": "site", "recompenses": []}


class Dates(unittest.TestCase):
    def test_jour_courant(self):
        self.assertEqual(avent.jour_courant(REGLAGES, le(30, mois=11)), 0)
        self.assertEqual(avent.jour_courant(REGLAGES, le(1, heure=0)), 1)
        self.assertEqual(avent.jour_courant(REGLAGES, le(7)), 7)
        self.assertEqual(avent.jour_courant(REGLAGES, le(25, heure=23)), 25)
        self.assertEqual(avent.jour_courant(REGLAGES, le(26)), -1)
        self.assertEqual(avent.jour_courant({**REGLAGES, "rattrapage_jusqu_au": "12-31"}, le(28)), 25)

    def test_heure_ouverture(self):
        r = {**REGLAGES, "heure_ouverture": "06:00"}
        self.assertEqual(avent.jour_courant(r, le(7, heure=5)), 6)
        self.assertEqual(avent.jour_courant(r, le(7, heure=6)), 7)


class Ouverture(unittest.TestCase):
    def test_ouvre_le_jour_et_donne_la_recompense(self):
        pb = Pb(base())
        eco = Eco(pb)
        ok, embed = run(avent.ouvrir(eco, membre(), None, maintenant=le(7)))
        self.assertTrue(ok, embed)
        self.assertEqual(eco.objets_donnes, [("o1", 1)])
        o = pb.d["avent_ouvertures"][0]
        self.assertEqual((o["jour"], o["source"], o["rattrapee"]), (7, "discord", False))
        self.assertTrue(o["recompenses"][0]["ok"])
        self.assertIn("Masque", embed["description"])

    def test_deja_ouverte_sur_le_site(self):
        pb = Pb(base([ouverture(7)]))
        eco = Eco(pb)
        ok, embed = run(avent.ouvrir(eco, membre(), None, maintenant=le(7)))
        self.assertFalse(ok)
        self.assertIn("déjà ouverte", embed["description"])
        self.assertEqual(eco.objets_donnes, [])

    def test_doublon_simultane_refuse_par_l_index(self):
        # Le site ouvre la fenêtre entre la vérification du bot et sa création : l'index unique refuse la création.
        pb = Pb(base([ouverture(7)]))
        eco = Eco(pb)
        vraie_premier, appels = pb.premier, []

        async def premier(col, filtre=None):
            if col == "avent_ouvertures" and not appels:
                appels.append(1)
                return None
            return await vraie_premier(col, filtre)
        pb.premier = premier
        ok, embed = run(avent.ouvrir(eco, membre(), None, maintenant=le(7)))
        self.assertFalse(ok)
        self.assertIn("déjà ouverte", embed["description"])
        self.assertEqual(eco.objets_donnes, [])
        self.assertEqual(len(pb.d["avent_ouvertures"]), 1)

    def test_fenetre_future_refusee(self):
        eco = Eco(Pb(base()))
        ok, embed = run(avent.ouvrir(eco, membre(), 12, maintenant=le(7)))
        self.assertFalse(ok)
        self.assertIn("couscous", embed["description"])

    def test_avant_le_premier_decembre(self):
        ok, embed = run(avent.ouvrir(Eco(Pb(base())), membre(), None, maintenant=le(30, mois=11)))
        self.assertFalse(ok)

    def test_rattrapage(self):
        pb = Pb(base())
        ok, _ = run(avent.ouvrir(Eco(pb), membre(), 3, maintenant=le(7)))
        self.assertTrue(ok)
        self.assertTrue(pb.d["avent_ouvertures"][0]["rattrapee"])

    def test_calendrier_ferme(self):
        d = base()
        d["avent_reglages"][0]["ouvert"] = False
        ok, embed = run(avent.ouvrir(Eco(Pb(d)), membre(), None, maintenant=le(7)))
        self.assertFalse(ok)
        self.assertIn("fermé", embed["description"])

    def test_apres_le_rattrapage(self):
        ok, embed = run(avent.ouvrir(Eco(Pb(base())), membre(), 3, maintenant=le(27)))
        self.assertFalse(ok)
        self.assertIn("fermé", embed["description"])

    def test_fragment_du_conte(self):
        ok, embed = run(avent.ouvrir(Eco(Pb(base())), membre(), None, maintenant=le(2)))
        self.assertTrue(ok)
        self.assertIn("Il neigeait sur Rostheim", embed["fields"][0]["value"])

    def test_echec_d_une_recompense_note_et_les_autres_donnees(self):
        d = base()
        d["avent_cases"][6]["recompenses"] = [{"type": "role", "role_nom": "Inconnu"},
                                              {"type": "objet", "objet": "o1", "objet_nom": "Masque"}]
        pb = Pb(d)
        eco = Eco(pb)
        ok, embed = run(avent.ouvrir(eco, membre(), None, maintenant=le(7)))
        self.assertTrue(ok)
        res = pb.d["avent_ouvertures"][0]["recompenses"]
        self.assertEqual([r["ok"] for r in res], [False, True])
        self.assertIn("pas encore donné", embed["description"])


class Recompenses(unittest.TestCase):
    """Mêmes écritures que le site (lib/aventServeur.ts)."""

    def avec(self, recompenses, **extra):
        d = base()
        d["avent_cases"][6]["recompenses"] = recompenses
        d.update(extra)
        pb = Pb(d)
        ok, embed = run(avent.ouvrir(Eco(pb), membre(), None, maintenant=le(7)))
        self.assertTrue(ok, embed)
        return pb

    def test_or_note_comme_le_site(self):
        pb = self.avec([{"type": "or", "quantite": 30}])
        self.assertEqual(pb.d["joueurs"][0]["eco_or"], 30)
        g = pb.d["eco_gains"][0]
        self.assertEqual((g["commande"], g["monnaie"], g["montant"]), ("Calendrier de l'Avent : case 7", "Or", 30))

    def test_objet_du_bureau_ajoute_a_possedes(self):
        pb = self.avec([{"type": "bureau", "objet": "bougie-bleue", "objet_nom": "Bougie bleue"}],
                       bureau_joueurs=[{"id": "b1", "pseudo": "Ràlas", "possedes": ["plume"], "poses": {"a": "plume"}}])
        b = pb.d["bureau_joueurs"][0]
        self.assertEqual(b["possedes"], ["plume", "bougie-bleue"])
        self.assertEqual(b["poses"], {"a": "plume"})

    def test_objet_du_bureau_sans_ligne(self):
        pb = self.avec([{"type": "bureau", "objet": "boule-cristal", "objet_nom": "Boule de cristal"}])
        self.assertEqual(pb.d["bureau_joueurs"], [{"id": pb.d["bureau_joueurs"][0]["id"], "pseudo": "Ràlas",
                                                    "possedes": ["boule-cristal"], "poses": {}}])

    def test_multiplicateur_commun_sans_doublon(self):
        rec = {"type": "multiplicateur", "facteur": 2, "monnaie": "xp", "salons": ["sj0dp5dwja7a633"], "jusqu": "minuit"}
        pb = self.avec([rec], eco_multiplicateurs=[])
        m = pb.d["eco_multiplicateurs"][0]
        self.assertTrue(m["libelle"].startswith("Avent : "))
        self.assertEqual((m["facteur_or"], m["facteur_xp"], m["portee"], m["salons"]), (1, 2.0, "salons", ["sj0dp5dwja7a633"]))
        # Un second joueur ouvre la même fenêtre : le multiplicateur déjà actif n'est pas recréé.
        pb.d["joueurs"].append({"id": "j2", "pseudo": "Kya", "discord_id": "222", "eco_or": 0})
        autre = membre()
        autre.id = 222
        with mock.patch.object(Eco, "joueur_de", new=lambda self, m: _j2(pb)):
            run(avent.ouvrir(Eco(pb), autre, None, maintenant=le(7)))
        self.assertEqual(len(pb.d["eco_multiplicateurs"]), 1)

    def test_lignes_notees_avant_les_recompenses(self):
        # Si le bot s'arrête pendant la distribution, l'ouverture garde des lignes « non données ».
        notes = []

        async def appliquer(eco, membre, joueur, rec, libelle, salon=None, maintenant=None):
            notes.append([dict(x) for x in pb.d["avent_ouvertures"][0]["recompenses"]])
        pb = Pb(base())
        with mock.patch.object(avent, "appliquer", appliquer):
            run(avent.ouvrir(Eco(pb), membre(), None, maintenant=le(7)))
        self.assertEqual(notes[0][0]["ok"], False)
        self.assertEqual(notes[0][0]["erreur"], "Non donnée : ouverture interrompue.")
        self.assertTrue(pb.d["avent_ouvertures"][0]["recompenses"][0]["ok"])


async def _j2(pb):
    return pb.d["joueurs"][1]


class Bonus(unittest.TestCase):
    def test_bonus_des_24_le_25(self):
        pb = Pb(base([ouverture(j) for j in range(1, 25)]))
        eco = Eco(pb)
        ok, embed = run(avent.ouvrir(eco, membre(), 25, maintenant=le(25)))
        self.assertTrue(ok)
        self.assertEqual(eco.roles_donnes, [("Citrouille", 5)])
        self.assertTrue(any(o["jour"] == 0 for o in pb.d["avent_ouvertures"]))
        self.assertIn("24 fenêtres", embed["fields"][-1]["value"])

    def test_pas_de_bonus_si_une_fenetre_rattrapee(self):
        pb = Pb(base([ouverture(j, rattrapee=(j == 4)) for j in range(1, 25)]))
        eco = Eco(pb)
        run(avent.ouvrir(eco, membre(), 25, maintenant=le(25)))
        self.assertEqual(eco.roles_donnes, [])

    def test_bonus_une_seule_fois(self):
        pb = Pb(base([ouverture(j) for j in range(1, 25)] + [ouverture(0)]))
        eco = Eco(pb)
        run(avent.ouvrir(eco, membre(), 25, maintenant=le(25)))
        self.assertEqual(eco.roles_donnes, [])


class Annonces(unittest.TestCase):
    def setUp(self):
        self.postes = []

        async def poster(salon, corps):
            self.postes.append((salon, corps))
            return True
        self.poster = poster

    def test_annonce_de_minuit_une_seule_fois(self):
        pb = Pb(base())
        eco = Eco(pb)
        self.assertEqual(run(avent.tour(eco, le(7, heure=0), self.poster)), ["annonce 2026-12-07"])
        self.assertEqual(run(avent.tour(eco, le(7, heure=1), self.poster)), [])
        salon, corps = self.postes[0]
        self.assertEqual(salon, "111")
        self.assertIn("7 décembre", corps["embeds"][0]["title"])
        self.assertIn("septième", corps["embeds"][0]["description"])
        self.assertEqual(corps["embeds"][0]["footer"]["text"], "Fêtes de Lanjis 1257 · aussi sur le site")

    def test_annonce_speciale_du_premier(self):
        eco = Eco(Pb(base()))
        run(avent.tour(eco, le(1, heure=0), self.poster))
        self.assertIn("commencent", self.postes[0][1]["embeds"][0]["description"])

    def test_rappel_mentionne_les_retardataires(self):
        pb = Pb(base([ouverture(6)]))
        pb.d["avent_reglages"][0]["derniere_annonce"] = "2026-12-07"
        eco = Eco(pb)
        with mock.patch.object(Pb, "requete", lambda self, m, c: _joueur(self, c)):
            fait = run(avent.tour(eco, le(7, heure=20), self.poster))
        self.assertEqual(fait, ["rappel 2026-12-07"])
        self.assertIn("<@123456789012345678>", self.postes[0][1]["content"])
        self.assertEqual(run(avent.tour(eco, le(7, heure=21), self.poster)), [])

    def test_pas_de_rappel_avant_l_heure(self):
        pb = Pb(base([ouverture(6)]))
        pb.d["avent_reglages"][0]["derniere_annonce"] = "2026-12-07"
        self.assertEqual(run(avent.tour(Eco(pb), le(7, heure=19), self.poster)), [])

    def test_hors_decembre_rien(self):
        self.assertEqual(run(avent.tour(Eco(Pb(base())), le(15, mois=11), self.poster)), [])


async def _joueur(pb, chemin):
    return next(j for j in pb.d["joueurs"] if j["id"] == chemin.rsplit("/", 1)[-1])


class Vues(unittest.TestCase):
    def test_calendrier_et_rattrapage(self):
        pb = Pb(base([ouverture(1), ouverture(2)]))
        vue = run(avent.vue_calendrier(Eco(pb), membre(), maintenant=le(5)))
        e = vue["embeds"][0]
        self.assertIn("**2** fenêtres ouvertes sur 24 · 2 à rattraper", e["description"])
        menu = vue["components"][-1]["components"][0]
        self.assertEqual([o["value"] for o in menu["options"]], ["3", "4"])

    def test_texte_recompenses(self):
        self.assertEqual(avent.texte_recompense({"type": "or", "quantite": 150}), "150 pièces d'or")
        self.assertEqual(avent.texte_recompense({"type": "role_temporaire", "role_nom": "Dranigba", "duree_jours": 5}),
                         "Rôle « Dranigba » pendant 5 jours")
        self.assertEqual(avent.texte_recompense({"type": "multiplicateur", "facteur": 3, "monnaie": "les_deux",
                                                 "salons": ["q"], "jusqu": "minuit"}),
                         "Or et XP ×3 sur 1 salon(s) jusqu'à minuit")


if __name__ == "__main__":
    unittest.main()
