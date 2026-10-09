"""Tests des drops automatiques (planificateur), de la durée par défaut de /drop et des loteries planifiées.
Lancer : python -m unittest discover -s tests
"""
import asyncio
import random
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from app import drop, drops_auto, loteries

PARIS = ZoneInfo("Europe/Paris")


def paris(*a) -> datetime:
    return datetime(*a, tzinfo=PARIS).astimezone(timezone.utc)


def heure_paris(d: datetime) -> tuple[int, int]:
    d = d.astimezone(PARIS)
    return d.hour, d.minute


QUOTIDIEN = {"id": "p1", "frequence": "quotidien", "heure_debut": "18:00", "heure_fin": "20:00"}
HEBDO_MERCREDI = {"id": "p2", "frequence": "hebdomadaire", "jour_semaine": 2, "heure_debut": "18:00", "heure_fin": "20:00"}


class Fenetre(unittest.TestCase):
    def test_tirage_toujours_dans_la_fenetre(self):
        rng = random.Random(1)
        for _ in range(500):
            h, fin = drops_auto.prochain_creneau(QUOTIDIEN, paris(2026, 10, 9, 10, 0), rng)
            self.assertTrue(paris(2026, 10, 9, 18, 0) <= h < paris(2026, 10, 9, 20, 0))
            self.assertEqual(fin, paris(2026, 10, 9, 20, 0))

    def test_tirage_reparti_sur_la_fenetre(self):
        rng = random.Random(2)
        heures = {heure_paris(drops_auto.prochain_creneau(QUOTIDIEN, paris(2026, 10, 9, 10, 0), rng)[0])[0] for _ in range(200)}
        self.assertEqual(heures, {18, 19})

    def test_fenetre_entamee_partie_restante(self):
        h, _ = drops_auto.prochain_creneau(QUOTIDIEN, paris(2026, 10, 9, 19, 30), random.Random(3))
        self.assertTrue(paris(2026, 10, 9, 19, 30) <= h < paris(2026, 10, 9, 20, 0))

    def test_fenetre_passee_lendemain(self):
        h, _ = drops_auto.prochain_creneau(QUOTIDIEN, paris(2026, 10, 9, 21, 0), random.Random(4))
        self.assertEqual(h.astimezone(PARIS).date().isoformat(), "2026-10-10")

    def test_fenetre_qui_passe_minuit(self):
        p = {"frequence": "quotidien", "heure_debut": "23:00", "heure_fin": "01:00"}
        h, fin = drops_auto.prochain_creneau(p, paris(2026, 10, 10, 0, 30), random.Random(5))
        self.assertTrue(paris(2026, 10, 10, 0, 30) <= h < paris(2026, 10, 10, 1, 0))
        self.assertEqual(fin, paris(2026, 10, 10, 1, 0))

    def test_heure_fixe(self):
        p = {"frequence": "quotidien", "heure_debut": "12:00", "heure_fin": "12:00"}
        self.assertEqual(drops_auto.prochain_creneau(p, paris(2026, 10, 9, 11, 0))[0], paris(2026, 10, 9, 12, 0))
        self.assertEqual(drops_auto.prochain_creneau(p, paris(2026, 10, 9, 12, 0))[0], paris(2026, 10, 10, 12, 0))

    def test_changement_d_heure(self):
        # Nuit du 24 au 25/10/2026 : passage à l'heure d'hiver ; la fenêtre reste 18 h – 20 h à Paris.
        h, fin = drops_auto.prochain_creneau(QUOTIDIEN, paris(2026, 10, 25, 10, 0), random.Random(6))
        self.assertEqual(heure_paris(fin), (20, 0))
        self.assertIn(heure_paris(h)[0], (18, 19))


class Frequence(unittest.TestCase):
    def test_hebdo_jour_choisi(self):
        rng = random.Random(7)
        # Vendredi 09/10/2026 → mercredi suivant 14/10.
        h, _ = drops_auto.prochain_creneau(HEBDO_MERCREDI, paris(2026, 10, 9, 10, 0), rng)
        self.assertEqual(h.astimezone(PARIS).date().isoformat(), "2026-10-14")
        self.assertEqual(h.astimezone(PARIS).weekday(), 2)

    def test_hebdo_une_fois_par_semaine(self):
        p, maintenant, rng, lances = dict(HEBDO_MERCREDI), paris(2026, 10, 9, 10, 0), random.Random(8), []
        while maintenant < paris(2026, 11, 6, 0, 0):  # 4 semaines, une vérification toutes les 20 s… par pas de 5 min
            maj, lancer = drops_auto.planifier(p, maintenant, rng)
            p.update(maj)
            if lancer:
                lances.append(maintenant)
            maintenant += timedelta(minutes=5)
        self.assertEqual([d.astimezone(PARIS).date().isoformat() for d in lances], ["2026-10-14", "2026-10-21", "2026-10-28", "2026-11-04"])
        for d in lances:
            self.assertIn(heure_paris(d)[0], (18, 19, 20))

    def test_quotidien_une_fois_par_jour(self):
        p, maintenant, rng, lances = dict(QUOTIDIEN), paris(2026, 10, 9, 10, 0), random.Random(9), []
        while maintenant < paris(2026, 10, 16, 10, 0):
            maj, lancer = drops_auto.planifier(p, maintenant, rng)
            p.update(maj)
            if lancer:
                lances.append(maintenant)
            maintenant += timedelta(minutes=1)
        self.assertEqual(len(lances), 7)
        self.assertEqual(len({d.astimezone(PARIS).date() for d in lances}), 7)

    def test_frequence_inconnue(self):
        self.assertIsNone(drops_auto.prochain_creneau({"frequence": "", "heure_debut": "18:00"}, paris(2026, 10, 9, 10, 0)))


class Redemarrage(unittest.TestCase):
    def test_creneau_consomme_avant_le_lancement(self):
        p = {**QUOTIDIEN, "prochain": "2026-10-09 16:30:00.000Z", "prochain_fin": "2026-10-09 18:00:00.000Z"}
        maj, lancer = drops_auto.planifier(p, paris(2026, 10, 9, 18, 31), random.Random(10))
        self.assertTrue(lancer)
        self.assertEqual(maj["dernier"], "2026-10-09 16:30:00.000Z")
        self.assertTrue(drops_auto.lire_date(maj["prochain"]) >= paris(2026, 10, 10, 18, 0))
        # Redémarrage juste après : la base contient déjà le créneau suivant → rien à relancer.
        p.update(maj)
        self.assertEqual(drops_auto.planifier(p, paris(2026, 10, 9, 18, 32)), ({}, False))

    def test_creneau_manque_saute(self):
        p = {**QUOTIDIEN, "prochain": "2026-10-09 16:30:00.000Z", "prochain_fin": "2026-10-09 18:00:00.000Z"}
        maj, lancer = drops_auto.planifier(p, paris(2026, 10, 9, 19, 50), random.Random(11))
        self.assertFalse(lancer)
        self.assertIn("sauté", maj["dernier_statut"])
        self.assertEqual(drops_auto.lire_date(maj["prochain"]).astimezone(PARIS).date().isoformat(), "2026-10-10")

    def test_modif_sur_le_site_pas_de_second_drop_dans_la_fenetre(self):
        # Drop déjà donné à 18:30 ; le planning est modifié à 18:45 (site : prochain vidé) → pas avant demain.
        p = {**QUOTIDIEN, "prochain": "", "dernier": "2026-10-09 16:30:00.000Z", "dernier_fin": "2026-10-09 18:00:00.000Z"}
        maj, lancer = drops_auto.planifier(p, paris(2026, 10, 9, 18, 45), random.Random(12))
        self.assertFalse(lancer)
        self.assertEqual(drops_auto.lire_date(maj["prochain"]).astimezone(PARIS).date().isoformat(), "2026-10-10")

    def test_tour_ecrit_avant_de_lancer(self):
        ordre = []
        p = {**QUOTIDIEN, "actif": True, "prochain": "2026-10-09 16:30:00.000Z", "prochain_fin": "2026-10-09 18:00:00.000Z", "nb_drops": 2}

        async def maj(col, id_, donnees):
            ordre.append(("maj", dict(donnees)))

        async def executer(eco, planning):
            ordre.append(("drop", planning["id"]))
            return "lancé"

        eco = SimpleNamespace(pb=SimpleNamespace(lister=mock.AsyncMock(return_value=[p]), maj=maj))
        with mock.patch.object(drops_auto, "executer", executer):
            asyncio.run(drops_auto.tour(eco, paris(2026, 10, 9, 18, 31)))
        self.assertEqual([o[0] for o in ordre], ["maj", "drop", "maj"])
        self.assertIn("prochain", ordre[0][1])
        self.assertEqual(ordre[2][1], {"dernier_statut": "lancé", "nb_drops": 3})


class DureeDrop(unittest.TestCase):
    def test_defaut_30_s(self):
        self.assertEqual(drop.duree_ou_defaut(None), 30)
        self.assertEqual(drop.duree_ou_defaut(0), 30)
        self.assertEqual(drop.duree_ou_defaut(90), 90)

    def _lancer(self, duree):
        cree = {}

        async def creer(col, donnees):
            cree.update(donnees)
            return {"id": "d1"}

        salon = SimpleNamespace(id=42, send=mock.AsyncMock(return_value=SimpleNamespace(id=7)))
        eco = SimpleNamespace(
            joueur_de=mock.AsyncMock(return_value={"id": "j1"}), _verrou=lambda _id: asyncio.Lock(),
            client=SimpleNamespace(get_channel=lambda _id: salon), pb=SimpleNamespace(creer=creer, maj=mock.AsyncMock()))

        async def go():
            with mock.patch.object(drop, "objet_par_nom", mock.AsyncMock(return_value={"id": "o1", "nom": "Pomme"})), \
                 mock.patch.object(drop, "quantite", mock.AsyncMock(return_value=(None, 5))), \
                 mock.patch.object(drop, "retirer_objet", mock.AsyncMock()), \
                 mock.patch.object(drop, "_attendre", mock.AsyncMock()):
                return await drop.lancer(eco, SimpleNamespace(id=1, mention="@K"), "o1", 1, duree, "42")
        ok, txt = asyncio.run(go())
        return ok, txt, cree

    def test_commande_sans_duree(self):
        ok, txt, cree = self._lancer(None)
        self.assertTrue(ok, txt)
        self.assertEqual(cree["duree_s"], 30)
        self.assertIn("30 s", txt)

    def test_commande_avec_duree(self):
        ok, _, cree = self._lancer(75)
        self.assertTrue(ok)
        self.assertEqual(cree["duree_s"], 75)

    def test_duree_hors_bornes(self):
        ok, _, _ = self._lancer(500)
        self.assertFalse(ok)

    def test_option_facultative_dans_la_commande(self):
        with mock.patch.dict("os.environ", {"DISCORD_TOKEN": "x", "DISCORD_APPLICATION_ID": "1"}):
            from scripts.register_commands import COMMANDS  # main() n'est pas appelé : rien n'est envoyé à Discord
        for nom in ("drop", "dropadmin"):
            c = next(c for c in COMMANDS if c["name"] == nom)
            duree = next(o for o in c["options"] if o["name"] == "duree")
            self.assertFalse(duree["required"])
            requis = [o["required"] for o in c["options"]]
            self.assertEqual(requis, sorted(requis, reverse=True), "options obligatoires en premier")


class LoteriePlanifiee(unittest.TestCase):
    def test_a_publier(self):
        maintenant = paris(2026, 10, 9, 15, 0)
        self.assertTrue(loteries.a_publier({"statut": "ouverte", "message_id": ""}, maintenant))
        self.assertTrue(loteries.a_publier({"statut": "ouverte", "message_id": "", "debut": ""}, maintenant))
        self.assertFalse(loteries.a_publier({"statut": "ouverte", "message_id": "", "debut": "2026-10-10 10:00:00.000Z"}, maintenant))
        self.assertTrue(loteries.a_publier({"statut": "ouverte", "message_id": "", "debut": "2026-10-09 10:00:00.000Z"}, maintenant))
        self.assertFalse(loteries.a_publier({"statut": "ouverte", "message_id": "123"}, maintenant))
        self.assertFalse(loteries.a_publier({"statut": "annulee", "message_id": ""}, maintenant))

    def test_duree_modele(self):
        self.assertEqual(loteries._duree_txt(1500), "1 j 1 h")
        self.assertEqual(loteries._duree_txt(45), "45 min")


if __name__ == "__main__":
    unittest.main()
