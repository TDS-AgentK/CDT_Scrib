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

    def test_heure_de_l_envoi(self):
        e = d.moment(date(2026, 11, 9), (6, 8, 0))  # dimanche 15 à 08:00, pour la semaine du 16
        self.assertEqual((e.weekday(), e.day, e.hour), (6, 15, 8))


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
        e = d.embed_jours(LUNDI, par_jour, pas_dispo, noms, 4)
        self.assertEqual(e["title"], "Inquisiteur des présences")
        self.assertIn("Vos disponibilités du 16 au 22 novembre", e["description"])
        self.assertIn("**Mar. 17** ⭐ · 3", e["description"])
        self.assertIn("Kelora, Fyb, Ràlas", e["description"])
        self.assertEqual(e["footer"]["text"], "4 réponses sur 4")  # ni rappel ni clôture
        self.assertEqual(d.lire_embed_jours(e), (LUNDI, False))
        ancien = d.embed_jours(LUNDI, par_jour, pas_dispo, noms, 4, clos=True)  # sondage clôturé avant le 16/11 : figé
        self.assertEqual(d.lire_embed_jours(ancien), (LUNDI, True))
        self.assertIsNone(d.lire_embed_jours({"title": "Autre chose"}))
        # Égalité au maximum : étoile sur chacun des soirs concernés.
        egalite = [[10], [10, 11], [10, 11], [10, 11], [], [], [10]]
        desc = d.embed_jours(LUNDI, egalite, [], noms, 4)["description"]
        self.assertEqual(desc.count("⭐"), 3)
        self.assertNotIn("⭐", d.embed_jours(LUNDI, [[] for _ in range(7)], [], noms, 4)["description"])
        # L'étoile suit le classement : un vote de plus pour jeudi la déplace.
        par_jour[3] = [10, 11, 12, 13]
        self.assertIn("**Jeu. 19** ⭐", d.embed_jours(LUNDI, par_jour, pas_dispo, noms, 4)["description"])


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
        e = d.embed_heures(d.titre_heures(date(2026, 11, 16), date(2026, 11, 16)), heures, arrivee, {10: "Kelora", 11: "Fyb"}, 2)
        self.assertEqual(e["title"], "À partir de quelle heure ?")  # ce soir : ni date ni horodatage
        self.assertNotIn("timestamp", e)
        self.assertEqual(d.titre_heures(date(2026, 11, 17), date(2026, 11, 16)), "Mardi 17 : à partir de quelle heure ?")
        lignes = e["description"].splitlines()
        self.assertTrue(any(l.startswith("🕖 **19:00** · 1") and "+ Kelora" in l for l in lignes))
        self.assertTrue(any(l.startswith("🕢 **19:30** · 1") and "+" not in l for l in lignes))
        self.assertTrue(any(l.startswith("🕗 **20:00** ⭐ · 2") and "+ Fyb" in l for l in lignes))
        self.assertIn("Tout le monde est là à partir de **20:00**.", e["description"])
        self.assertEqual(d.lire_embed_heures(e), ("À partir de quelle heure ?", heures))

    def test_choix_des_soirs(self):
        c = d.choix_soirs(date(2026, 11, 16), 4)
        self.assertEqual(c, [("Ce soir (Lun. 16)", "2026-11-16"), ("Demain (Mar. 17)", "2026-11-17"), ("Mer. 18", "2026-11-18"), ("Jeu. 19", "2026-11-19")])

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
            self.assertNotIn("rappel", cfg)
            self.assertNotIn("cloture", cfg)
            self.assertEqual((cfg["debut"], cfg["fin"], cfg["pas"]), ("20:00", "21:00", 60))
        perms = SimpleNamespace(manage_messages=False, add_reactions=True, send_messages=True, embed_links=True, read_message_history=True)
        self.assertEqual(d.texte_verif(perms, "séances", datetime(2026, 11, 16, 20, 0)), "Il manque au bot dans #séances : Gérer les messages.")
        perms.manage_messages = True
        self.assertTrue(d.texte_verif(perms, "séances", datetime(2026, 11, 16, 20, 0)).startswith("ok dans #séances"))



class MiseAJour(unittest.TestCase):
    def test_votes_regroupes(self):
        """Plusieurs votes rapprochés : une seule modification du message, après ~2 s."""
        import asyncio
        from unittest import mock

        async def scenario():
            dispos = d.Disponibilites.__new__(d.Disponibilites)
            dispos._taches, dispos._verrous = {}, {}
            appels = []

            async def maj(salon, mid):
                await asyncio.sleep(0.05)
                appels.append(mid)
            dispos._mettre_a_jour = maj
            for _ in range(5):
                dispos._planifier(None, 42)
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.2)
            return appels
        self.assertEqual(asyncio.run(scenario()), [42])


if __name__ == "__main__":
    unittest.main()
