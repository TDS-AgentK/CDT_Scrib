"""Annonce de montée de niveau : postée dans #général (salon des niveaux), quel que soit le salon du message.
Lancer : python -m unittest discover -s tests
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from app import economie
from app.economie import Economie

GUILD = SimpleNamespace(id=1)


def salon(sid, guild=GUILD, echoue=False):
    s = SimpleNamespace(id=sid, name=f"s{sid}", guild=guild, envoyes=[])

    async def send(embed=None, file=None, **k):
        if echoue:
            raise RuntimeError("Missing Permissions")
        s.envoyes.append(embed.description)
        s.fichiers = getattr(s, "fichiers", []) + [file]
    s.send = send
    return s


def annoncer(reglages=None, salons=None, recompenses=None, fetch=None):
    salons = salons or {}
    eco = Economie.__new__(Economie)
    eco._niveaux_vus, eco._hausses, eco._recompenses_max = {}, {}, {}
    eco.pb = SimpleNamespace()
    eco.client = SimpleNamespace(get_channel=lambda i: salons.get(i),
                                 fetch_channel=mock.AsyncMock(side_effect=fetch or RuntimeError("introuvable")))
    eco.config = mock.AsyncMock(return_value={"reglages": reglages or {}, "recompenses": recompenses or [], "paliers": []})
    eco.donner_recompense = mock.AsyncMock(side_effect=RuntimeError("rôle au-dessus du bot"))
    eco.donner_succes = mock.AsyncMock()
    origine = salon(99)
    membre = SimpleNamespace(mention="<@7>", name="Mirelle", guild=GUILD)
    with mock.patch("app.journal.noter", mock.AsyncMock()):
        asyncio.run(eco.monter_niveau(origine, membre, {"id": "j1"}, 2, 3))
    return origine


class AnnonceNiveau(unittest.TestCase):
    def test_annonce_dans_general(self):
        general = salon(int(economie.SALON_NIVEAUX_DEFAUT))
        origine = annoncer(salons={general.id: general})
        self.assertEqual(general.envoyes, ["<@7> atteint le niveau **3** (XP)."])
        self.assertEqual(origine.envoyes, [])

    def test_salon_des_reglages_prioritaire(self):
        autre = salon(555)
        annoncer(reglages={"salon_niveaux_id": "555"}, salons={555: autre})
        self.assertEqual(len(autre.envoyes), 1)

    def test_salon_introuvable_repli_sur_origine(self):
        origine = annoncer(salons={})
        self.assertEqual(len(origine.envoyes), 1)

    def test_autre_serveur_repli_sur_origine(self):
        general = salon(int(economie.SALON_NIVEAUX_DEFAUT), guild=SimpleNamespace(id=2))
        origine = annoncer(salons={general.id: general})
        self.assertEqual((general.envoyes, len(origine.envoyes)), ([], 1))

    def test_permission_refusee_repli_sur_origine(self):
        general = salon(int(economie.SALON_NIVEAUX_DEFAUT), echoue=True)
        origine = annoncer(salons={general.id: general})
        self.assertEqual(len(origine.envoyes), 1)

    def test_recompense_en_echec_n_empeche_pas_l_annonce(self):
        general = salon(int(economie.SALON_NIVEAUX_DEFAUT))
        annoncer(salons={general.id: general}, recompenses=[{"niveau": 3, "recompense_role": "Chroniqueur"}])
        self.assertEqual(len(general.envoyes), 1)


class AnnonceIllustree(unittest.TestCase):
    def test_banniere_jointe(self):
        general = salon(int(economie.SALON_NIVEAUX_DEFAUT))
        eco = Economie.__new__(Economie)
        eco._niveaux_vus, eco._hausses, eco._recompenses_max = {}, {}, {}
        eco.pb = SimpleNamespace(url="")
        eco.client = SimpleNamespace(get_channel=lambda i: general)
        eco.config = mock.AsyncMock(return_value={"reglages": {}, "recompenses": [], "paliers": []})
        membre = SimpleNamespace(mention="<@7>", name="Mirelle", display_name="Mirelle", guild=GUILD, display_avatar=None)
        with mock.patch("app.journal.noter", mock.AsyncMock()), \
                mock.patch("app.chemin_rangs._telecharger", mock.AsyncMock(return_value=None)), \
                mock.patch("app.chemin_rangs.succes_par_id", mock.AsyncMock(return_value={})):
            asyncio.run(eco.monter_niveau(salon(99), membre, {"id": "j1"}, 2, 3))
        self.assertEqual(len(general.envoyes), 1)
        self.assertIn("**3**", general.envoyes[0])
        self.assertEqual(general.fichiers[0].filename, "niveau.png")


if __name__ == "__main__":
    unittest.main()


class SurveillanceNiveaux(unittest.TestCase):
    """XP modifiée hors des gains du bot (page Économie du site) : annonce après deux passages."""
    COURBE = {"formule": "personnalisee", "base_xp": 100, "pas_xp": 0, "courbure": 0, "arrondi": 1}

    def preparer(self):
        self.joueurs = [{"id": "j1", "pseudo": "Kelora", "discord_id": "7", "eco_xp": 250}]
        eco = Economie.__new__(Economie)
        eco._niveaux_vus, eco._hausses, eco._recompenses_max = {}, {}, {}
        eco.guild_id = None
        membre = SimpleNamespace(mention="<@7>", name="Kelora", guild=GUILD)
        guild = SimpleNamespace(id=1, get_member=lambda i: membre)
        general = SimpleNamespace(id=int(economie.SALON_NIVEAUX_DEFAUT), guild=guild)
        eco.client = SimpleNamespace(get_channel=lambda i: general, get_guild=lambda i: None)

        async def lister(col, filtre=None, tri=None, expand=None):
            return [dict(j) for j in self.joueurs]
        eco.pb = SimpleNamespace(lister=lister)
        eco.config = mock.AsyncMock(return_value={"reglages": {}, "courbe": self.COURBE})
        eco.monter_niveau = mock.AsyncMock()
        return eco

    def passe(self, eco):
        asyncio.run(eco.surveiller_niveaux())

    def test_hausse_annoncee_au_second_passage(self):
        eco = self.preparer()
        self.passe(eco)                      # base : niveau 2
        self.joueurs[0]["eco_xp"] = 450      # niveau 4 (modifié sur le site)
        self.passe(eco)
        eco.monter_niveau.assert_not_awaited()
        self.passe(eco)
        eco.monter_niveau.assert_awaited_once()
        _, _, j, avant, apres = eco.monter_niveau.await_args.args
        self.assertEqual((j["id"], avant, apres), ("j1", 2, 4))

    def test_deja_annonce_par_un_gain(self):
        eco = self.preparer()
        self.passe(eco)
        self.joueurs[0]["eco_xp"] = 450
        self.passe(eco)
        eco._niveaux_vus["j1"] = 4           # le gain de message l'a annoncé entre-temps
        self.passe(eco)
        eco.monter_niveau.assert_not_awaited()

    def test_retrait_puis_ajout_reannonce(self):
        eco = self.preparer()
        self.passe(eco)
        self.joueurs[0]["eco_xp"] = 50       # retrait : niveau 0, rien d'annoncé
        self.passe(eco); self.passe(eco)
        eco.monter_niveau.assert_not_awaited()
        self.joueurs[0]["eco_xp"] = 250      # ajout : retour au niveau 2
        self.passe(eco); self.passe(eco)
        _, _, _, avant, apres = eco.monter_niveau.await_args.args
        self.assertEqual((avant, apres), (0, 2))


class RecompensesPasRedonnees(unittest.TestCase):
    def test_niveau_regagne_sans_recompense(self):
        general = salon(int(economie.SALON_NIVEAUX_DEFAUT))
        eco = Economie.__new__(Economie)
        eco._niveaux_vus, eco._hausses, eco._recompenses_max = {}, {}, {"j1": 3}
        eco.pb = SimpleNamespace(url="")
        eco.client = SimpleNamespace(get_channel=lambda i: general)
        eco.config = mock.AsyncMock(return_value={"reglages": {}, "recompenses": [{"niveau": 3, "recompense_or": 10}], "paliers": []})
        eco.donner_recompense = mock.AsyncMock(return_value="10 Or")
        eco.donner_succes = mock.AsyncMock()
        membre = SimpleNamespace(mention="<@7>", name="M", guild=GUILD)
        with mock.patch("app.journal.noter", mock.AsyncMock()), \
                mock.patch("app.chemin_rangs.succes_famille_niveau", mock.AsyncMock(return_value={})):
            asyncio.run(eco.monter_niveau(salon(99), membre, {"id": "j1"}, 2, 3))
        eco.donner_recompense.assert_not_awaited()
        self.assertEqual(len(general.envoyes), 1)
