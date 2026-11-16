"""Tests des sondages de disponibilités (calculs purs : rien n'est posté sur Discord).
Lancer : python -m unittest discover -s tests
"""
import unittest
from datetime import date, datetime

from app import disponibilites as d

LUNDI = date(2026, 11, 16)
BOT = 1


class Dates(unittest.TestCase):
    def test_semaine_suivante_et_periode(self):
        self.assertEqual(d.lundi_suivant(date(2026, 11, 15)), LUNDI)  # posté le dimanche 15
        self.assertEqual(d.lundi_suivant(date(2026, 11, 16)), date(2026, 11, 23))
        self.assertEqual(d.periode(LUNDI), "du 16 au 22 novembre")
        self.assertEqual(d.periode(date(2026, 11, 30)), "du 30 novembre au 6 décembre")
        self.assertEqual(d.periode(date(2025, 12, 29)), "du 29 décembre 2025 au 4 janvier 2026")
        self.assertEqual(d.jour_court(LUNDI), "Lun. 16")

    def test_cloture_et_rappel(self):
        c = d.moment(LUNDI, (5, 12, 0))
        self.assertEqual((c.weekday(), c.day, c.hour), (5, 21, 12))
        r = d.moment(LUNDI, (4, 18, 0))
        self.assertEqual((r.weekday(), r.day, r.hour), (4, 20, 18))


class Jours(unittest.TestCase):
    REACTIONS = {"1️⃣": [BOT, 10], "2️⃣": [BOT, 10, 11, 12], "3️⃣": [BOT, 12], "4️⃣": [BOT], "5️⃣": [BOT], "6️⃣": [BOT],
                 "7️⃣": [BOT], "❌": [BOT, 13]}

    def test_etat_sans_le_bot(self):
        par_jour, pas_dispo, repondants = d.etat_jours(self.REACTIONS, BOT)
        self.assertEqual(par_jour[1], [10, 11, 12])
        self.assertEqual(pas_dispo, [13])
        self.assertEqual(sorted(repondants), [10, 11, 12, 13])
        self.assertEqual(d.meilleur_soir(par_jour), 1)
        self.assertIsNone(d.meilleur_soir([[] for _ in range(7)]))

    def test_embed_et_relecture(self):
        par_jour, pas_dispo, rep = d.etat_jours(self.REACTIONS, BOT)
        noms = {10: "Kelora", 11: "Fyb", 12: "Ràlas", 13: "Kyanite"}
        e = d.embed_jours(LUNDI, par_jour, pas_dispo, noms, 4, d.moment(LUNDI, (5, 12, 0)))
        self.assertEqual(e["title"], "Inquisiteur des présences")
        self.assertIn("Vos disponibilités du 16 au 22 novembre", e["description"])
        self.assertIn("**Mar. 17** ⭐ · 3", e["description"])
        self.assertIn("Kelora, Fyb, Ràlas", e["description"])
        self.assertEqual(e["footer"]["text"], "4 réponses sur 4 · clôture samedi 21 à 12:00")
        self.assertEqual(d.lire_embed_jours(e), (LUNDI, False, False))
        clos = d.embed_jours(LUNDI, par_jour, pas_dispo, noms, 4, d.moment(LUNDI, (5, 12, 0)), clos=True, rappel=True)
        self.assertEqual(d.lire_embed_jours(clos), (LUNDI, True, True))
        self.assertIsNone(d.lire_embed_jours({"title": "Autre chose"}))
        self.assertIn("Meilleur soir : **Mardi 17**", d.texte_cloture(LUNDI, par_jour, noms))


class Heures(unittest.TestCase):
    def test_creneaux_et_horloges(self):
        self.assertEqual(d.creneaux(), ["19:00", "19:30", "20:00", "20:30", "21:00"])
        self.assertEqual(d.creneaux("19h", "20h", 60), ["19:00", "20:00"])
        self.assertEqual([d.horloge(h) for h in d.creneaux()], ["🕖", "🕢", "🕗", "🕣", "🕘"])
        self.assertEqual(d.horloge("00:00"), "🕛")
        with self.assertRaises(ValueError):
            d.creneaux("19:15", "21:00")

    def test_cumul_nouveaux_et_etoile(self):
        heures = d.creneaux()
        reactions = {"🕖": [BOT, 10], "🕢": [BOT], "🕗": [BOT, 11, 10], "🕣": [BOT], "🕘": [BOT]}
        arrivee = d.arrivees(reactions, heures, BOT)
        self.assertEqual(arrivee, {10: 0, 11: 2})  # deux réactions restantes : la plus tôt compte
        e = d.embed_heures(date(2026, 11, 17), heures, arrivee, {10: "Kelora", 11: "Fyb"}, 2)
        self.assertEqual(e["title"], "Mardi 17 : à partir de quelle heure ?")
        lignes = e["description"].splitlines()
        self.assertTrue(any(l.startswith("🕖 **19:00** · 1") and "+ Kelora" in l for l in lignes))
        self.assertTrue(any(l.startswith("🕢 **19:30** · 1") and "+" not in l for l in lignes))
        self.assertTrue(any(l.startswith("🕗 **20:00** ⭐ · 2") and "+ Fyb" in l for l in lignes))
        self.assertIn("Tout le monde est là à partir de **20:00**.", e["description"])
        self.assertEqual(d.lire_embed_heures(e), (date(2026, 11, 17), heures))

    def test_soir_choisi(self):
        self.assertEqual(d.soir_choisi("Mardi", date(2026, 11, 16)), date(2026, 11, 17))
        self.assertEqual(d.soir_choisi("Lundi", date(2026, 11, 16)), date(2026, 11, 16))
        self.assertIsNone(d.soir_choisi(None, date(2026, 11, 16)))


class Reglages(unittest.TestCase):
    def test_site_puis_variables(self):
        from types import SimpleNamespace
        from unittest import mock
        with mock.patch.dict("os.environ", {"DISPO_ACTIF": "1", "DISPO_SALON_ID": "111", "DISPO_ROLE_ID": "222"}, clear=False):
            self.assertEqual(d.reglages_depuis(None)["salon_id"], 111)  # champs absents : variables d'environnement
            self.assertTrue(d.reglages_depuis({"id": "x"})["actif"])  # base sans les champs dispo_* : idem
            fiche = {"id": "x", "dispo_actif": False, "dispo_salon_id": "333", "dispo_seances_id": "", "dispo_role_id": "",
                     "dispo_mention": True, "dispo_envoi_jour": 0, "dispo_envoi_heure": "09:30", "dispo_rappel_jour": None,
                     "dispo_rappel_heure": "", "dispo_cloture_jour": 6, "dispo_cloture_heure": "", "dispo_heure_debut": "20:00",
                     "dispo_heure_fin": "", "dispo_pas": 60}
            cfg = d.reglages_depuis(fiche)
            self.assertFalse(cfg["actif"])
            self.assertTrue(cfg["mention"])
            self.assertEqual(cfg["salon_id"], 333)
            self.assertEqual(cfg["role_id"], 222)  # vide sur le site : variable d'environnement
            self.assertEqual(cfg["envoi"], (0, 9, 30))  # lundi 0 accepté
            self.assertEqual(cfg["rappel"], (4, 18, 0))
            self.assertEqual(cfg["cloture"], (6, 12, 0))
            self.assertEqual((cfg["debut"], cfg["fin"], cfg["pas"]), ("20:00", "21:00", 60))
        perms = SimpleNamespace(manage_messages=False, add_reactions=True, send_messages=True, embed_links=True, read_message_history=True)
        self.assertEqual(d.texte_verif(perms, "séances", datetime(2026, 11, 16, 20, 0)), "Il manque au bot dans #séances : Gérer les messages.")
        perms.manage_messages = True
        self.assertTrue(d.texte_verif(perms, "séances", datetime(2026, 11, 16, 20, 0)).startswith("ok dans #séances"))



if __name__ == "__main__":
    unittest.main()
