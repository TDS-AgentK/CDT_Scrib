"""Gain personnalisé des loteries : affiché, jamais distribué sur Discord.
Lancer : python -m unittest discover -s tests
"""
import asyncio
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from app import loteries


def tirer(loterie: dict, tickets: list[dict]):
    envoyes, maj = [], []
    joueurs = {"jA": {"id": "jA", "discord_id": "7"}, "jB": {"id": "jB", "discord_id": "8"}}

    async def requete(m, chemin):
        return dict(loterie) if "eco_loteries" in chemin else joueurs[chemin.rsplit("/", 1)[1]]

    async def send(embed=None, **k):
        envoyes.append(embed.description)
    salon = SimpleNamespace(guild=None, send=send)
    eco = SimpleNamespace(pb=SimpleNamespace(requete=requete, lister=mock.AsyncMock(return_value=tickets), maj=mock.AsyncMock(side_effect=lambda c, i, d: maj.append(d))),
                          client=SimpleNamespace(get_channel=lambda i: salon, get_guild=lambda i: None), guild_id=None,
                          config=mock.AsyncMock(return_value={"reglages": {}}), donner_succes=mock.AsyncMock(), ajouter_objet=mock.AsyncMock(),
                          donner_role=mock.AsyncMock())
    with mock.patch.object(loteries, "changer_or", mock.AsyncMock()) as or_, mock.patch.object(loteries, "_gain", mock.AsyncMock()), \
            mock.patch.object(loteries.eco_vues, "or_txt", lambda c: "Or"):
        asyncio.run(loteries.tirer(eco, loterie))
    return envoyes, maj, eco, or_


BASE = {"id": "L1", "titre": "Gain", "statut": "ouverte", "nombre_gagnants": 2, "salon_id": "1", "message_id": "",
        "or_lot": 0, "objet": "", "role_id": "", "succes": ""}


class GainPerso(unittest.TestCase):
    def test_texte_du_lot(self):
        with mock.patch.object(loteries.eco_vues, "or_txt", lambda c: "Or"):
            self.assertEqual(loteries._lot_txt({"gain_perso": "  Un dessin de Kyanite "}, None, {}), "🎁 Un dessin de Kyanite")
            self.assertIn("**10** Or + 🎁 Une partie privée", loteries._lot_txt({"or_lot": 10, "gain_perso": "Une partie privée"}, None, {}))
            self.assertEqual(loteries._lot_txt({"gain_perso": "   "}, None, {}), "—")

    def test_tirage_annonce_sans_rien_distribuer(self):
        envoyes, maj, eco, or_ = tirer({**BASE, "gain_perso": "Un dessin"}, [{"joueur": "jA", "quantite": 1}, {"joueur": "jB", "quantite": 1}])
        self.assertIn("🎁 Gain personnalisé : Un dessin — remis par un administrateur.", envoyes[0])
        self.assertEqual(or_.await_count, 0)
        eco.ajouter_objet.assert_not_awaited()
        eco.donner_role.assert_not_awaited()
        self.assertEqual(sorted(next(d for d in maj if "gagnants_ids" in d)["gagnants_ids"].split(",")), ["jA", "jB"])

    def test_personne_na_participe(self):
        envoyes, *_ = tirer({**BASE, "gain_perso": "Un dessin"}, [])
        self.assertEqual(envoyes[0], "Personne n'a participé.")

    def test_modeles(self):
        self.assertIn("gain_perso", loteries.CHAMPS_MODELE)

    def test_creer_avec_seulement_un_gain(self):
        cree = {}

        async def creer(col, d):
            cree.update(d)
            return {**d, "id": "L9"}
        eco = SimpleNamespace(pb=SimpleNamespace(creer=creer), config=mock.AsyncMock(return_value={"reglages": {}}))
        fin = (datetime.now(loteries.PARIS) + timedelta(days=1)).strftime("%d/%m/%Y %H:%M")
        with mock.patch.object(loteries, "est_admin", mock.AsyncMock(return_value=True)), mock.patch.object(loteries, "publier", mock.AsyncMock()):
            ok, txt = asyncio.run(loteries.creer(eco, SimpleNamespace(id=1, guild=None), {"titre": "T", "fin": fin, "gain": "  Un dessin  "}, "5"))
            self.assertTrue(ok, txt)
            self.assertEqual(cree["gain_perso"], "Un dessin")
            ok, txt = asyncio.run(loteries.creer(eco, SimpleNamespace(id=1, guild=None), {"titre": "T", "fin": fin, "gain": "   "}, "5"))
            self.assertFalse(ok)
            self.assertIn("gain personnalisé", txt)

    def test_option_de_commande(self):
        with mock.patch.dict("os.environ", {"DISCORD_TOKEN": "x", "DISCORD_APPLICATION_ID": "1"}):
            from scripts.register_commands import COMMANDS
        creer = next(o for o in next(c for c in COMMANDS if c["name"] == "loterie")["options"] if o["name"] == "creer")
        gain = next(o for o in creer["options"] if o["name"] == "gain")
        self.assertEqual(gain["type"], 3)
        self.assertFalse(gain["required"])
        self.assertLessEqual(len(creer["options"]), 25)


if __name__ == "__main__":
    unittest.main()
