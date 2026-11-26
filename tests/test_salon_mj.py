"""Tests des salons MJ éphémères (/salon_mj) : texte, page d'archive, images réduites, balayage après 24 h.
Lancer : python -m unittest discover -s tests
"""
import asyncio
import io
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

import discord

from app import salon_mj

T0 = 1_800_000_000  # heure de référence des tests


def _snowflake(ts: float) -> int:
    return (int(ts * 1000) - salon_mj.DISCORD_EPOCH_MS) << 22


class FauxPB:
    def __init__(self, fiches):
        self.fiches = {f["id"]: f for f in fiches}
        self.supprimes, self.maj_faites = [], []

    async def lister(self, collection, filtre=None, tri=None, expand=None):
        assert collection == salon_mj.COLLECTION
        if filtre.startswith('id="'):
            return [f for f in self.fiches.values() if f["id"] == filtre[4:-1]]
        if filtre == 'statut="ouvert"':
            return [f for f in self.fiches.values() if f["statut"] == "ouvert"]
        if filtre.startswith("expire"):
            limite = filtre.split('expire<"')[1].rstrip('"')
            return [f for f in self.fiches.values() if f.get("expire") and f["expire"] < limite]
        return [f for f in self.fiches.values()
                if f["statut"] == "ouvert" or (f["statut"] == "archive" and not f.get("supprime_le"))]

    async def maj(self, collection, id_, donnees):
        self.maj_faites.append((id_, donnees))
        self.fiches[id_].update(donnees)
        return self.fiches[id_]

    async def supprimer(self, collection, id_):
        self.supprimes.append(id_)
        self.fiches.pop(id_)


class FauxSalon:
    def __init__(self, ident, dernier_ts, nom="la-tour"):
        self.id = ident
        self.name = nom
        self.last_message_id = _snowflake(dernier_ts)
        self.created_at = datetime.fromtimestamp(dernier_ts - 3600, timezone.utc)
        self.supprime = False
        self.envois = []

    async def delete(self, reason=None):
        self.supprime = True

    async def send(self, **kw):
        self.envois.append(kw)
        return mock.Mock(id=999)


class FauxClient:
    def __init__(self, salons):
        self.salons = salons

    def get_channel(self, ident):
        return self.salons.get(ident)

    async def fetch_channel(self, ident):
        if ident not in self.salons:
            raise discord.NotFound(mock.Mock(status=404, reason="Not Found"), {"code": 10003, "message": "Unknown Channel"})
        return self.salons[ident]


class TexteTest(unittest.TestCase):
    def test_nom_salon(self):
        self.assertEqual(salon_mj.nom_salon("  La Tour d'Ébène !  "), "la-tour-débène")
        self.assertEqual(salon_mj.nom_salon("???"), "salon-mj")

    def test_texte_echappe_et_mis_en_forme(self):
        h = salon_mj.rendre_texte("<script>x</script> **gras** *ital* ||secret|| <@12> https://ex.fr/a_b_c", {"u12": "Kyanite"})
        self.assertNotIn("<script>", h)
        self.assertIn("&lt;script&gt;", h)
        self.assertIn("<strong>gras</strong>", h)
        self.assertIn("<em>ital</em>", h)
        self.assertIn('class="spoiler"', h)
        self.assertIn("@Kyanite", h)
        self.assertIn('href="https://ex.fr/a_b_c"', h)  # pas d'italique dans les liens

    def test_code_non_interprete(self):
        h = salon_mj.rendre_texte("`**pas gras**` et\n> citation")
        self.assertIn("<code>**pas gras**</code>", h)
        self.assertIn('class="citation"', h)

    def test_heure_snowflake(self):
        self.assertAlmostEqual(salon_mj.heure_snowflake(_snowflake(T0)), T0, delta=0.01)


class PageTest(unittest.TestCase):
    def test_page_autonome(self):
        d = datetime.fromtimestamp(T0, timezone.utc)
        msgs = [{"auteur_id": 1, "auteur": "Fyb <b>", "avatar": None, "date": d, "texte_html": "Bonjour",
                 "images": ["data:image/webp;base64,AAAA"], "fichiers": [("notes.txt", "data:text/plain;base64,QQ==", 1)], "embeds": []}]
        h = salon_mj.page_html({"titre": "La Tour", "salon": "la-tour", "createur": "Fyb", "debut": d, "fin": d, "genere": d}, msgs)
        self.assertTrue(h.startswith("<!DOCTYPE html>"))
        self.assertIn("Fyb &lt;b&gt;", h)
        self.assertIn('src="data:image/webp;base64,AAAA"', h)
        self.assertIn('download="notes.txt"', h)
        self.assertNotIn("cdn.discordapp.com", h)


class ImageTest(unittest.TestCase):
    def test_grande_image_reduite(self):
        from PIL import Image
        img = Image.effect_noise((3000, 2000), 80).convert("RGB")
        brut = io.BytesIO()
        img.save(brut, "PNG")
        octets, mime = salon_mj.reduire_image(brut.getvalue())
        self.assertEqual(mime, "image/webp")
        self.assertLess(len(octets), len(brut.getvalue()))
        self.assertEqual(max(Image.open(io.BytesIO(octets)).size), salon_mj.IMAGE_COTE_MAX)

    def test_pas_une_image(self):
        with self.assertRaises(Exception):
            salon_mj.reduire_image(b"pas une image")


class BalayageTest(unittest.TestCase):
    def _eco(self, fiches, salons):
        return mock.Mock(pb=FauxPB(fiches), client=FauxClient(salons))

    def test_archive_apres_24h_seulement(self):
        actif = FauxSalon(1, T0 - 3600)
        inactif = FauxSalon(2, T0 - 25 * 3600)
        archives = FauxSalon(50, T0)
        eco = self._eco([
            {"id": "a", "salon_id": "1", "statut": "ouvert", "titre": "Actif"},
            {"id": "b", "salon_id": "2", "statut": "ouvert", "titre": "Inactif", "createur_nom": "Fyb", "createur_discord_id": "7"},
        ], {1: actif, 2: inactif, 50: archives})
        depots = []

        async def deposer(pb, fiche_id, chemin, nom, champs):
            self.assertTrue(os.path.isfile(chemin))
            depots.append(chemin)
            return await pb.maj(salon_mj.COLLECTION, fiche_id, {**champs, "fichier": nom})

        async def lire(salon):
            return []

        with mock.patch.dict(os.environ, {"SALON_MJ_ARCHIVES_ID": "50"}), \
             mock.patch.object(salon_mj, "deposer_archive", deposer), mock.patch.object(salon_mj, "lire_salon", lire):
            asyncio.run(salon_mj.balayer(eco, T0))
        self.assertFalse(actif.supprime)
        self.assertTrue(inactif.supprime)
        self.assertEqual(len(depots), 1)
        self.assertFalse(os.path.exists(os.path.dirname(depots[0])))  # dossier temporaire effacé
        fiche = eco.pb.fiches["b"]
        self.assertEqual(fiche["statut"], "archive")
        self.assertTrue(fiche["supprime_le"])
        self.assertGreaterEqual(len(fiche["cle"]), 32)
        lien = archives.envois[0]["embed"].url
        self.assertEqual(lien, salon_mj.lien_archive(fiche["cle"]))

    def test_sans_salon_archive_rien_n_est_supprime(self):
        inactif = FauxSalon(2, T0 - 25 * 3600)
        eco = self._eco([{"id": "b", "salon_id": "2", "statut": "ouvert"}], {2: inactif})
        with mock.patch.dict(os.environ, {"SALON_MJ_ARCHIVES_ID": ""}):
            asyncio.run(salon_mj.balayer(eco, T0))
        self.assertFalse(inactif.supprime)
        self.assertEqual(eco.pb.fiches["b"]["statut"], "ouvert")

    def test_salon_efface_a_la_main_et_archives_perimees(self):
        eco = self._eco([
            {"id": "x", "salon_id": "9", "statut": "ouvert"},
            {"id": "vieux", "salon_id": "3", "statut": "archive", "supprime_le": "2026-01-01", "expire": "2027-01-01 00:00:00.000Z"},
        ], {})
        asyncio.run(salon_mj.balayer(eco, datetime(2027, 2, 1, tzinfo=timezone.utc).timestamp()))
        self.assertEqual(eco.pb.fiches["x"]["statut"], "disparu")
        self.assertEqual(eco.pb.supprimes, ["vieux"])


class ClotureTest(unittest.TestCase):
    def _preparer(self):
        salon = FauxSalon(2, T0 - 60)  # actif : la clôture n'attend pas 24 h
        archives = FauxSalon(50, T0)
        pb = FauxPB([{"id": "b", "salon_id": "2", "statut": "ouvert", "titre": "La Tour", "createur_nom": "Fyb", "createur_discord_id": "7"}])
        return mock.Mock(pb=pb, client=FauxClient({2: salon, 50: archives})), salon, archives

    async def _deposer(self, pb, fiche_id, chemin, nom, champs):
        return await pb.maj(salon_mj.COLLECTION, fiche_id, {**champs, "fichier": nom})

    async def _lire(self, salon):
        return []

    def _cloturer(self, eco, user_id, admin, valeur, salon_courant=None):
        membre = mock.Mock(id=user_id)
        with mock.patch.dict(os.environ, {"SALON_MJ_ARCHIVES_ID": "50"}),              mock.patch.object(salon_mj, "deposer_archive", self._deposer), mock.patch.object(salon_mj, "lire_salon", self._lire):
            return asyncio.run(salon_mj.cloturer(eco, membre, admin, valeur, salon_courant))

    def test_createur_cloture_tout_de_suite(self):
        eco, salon, archives = self._preparer()
        txt = self._cloturer(eco, 7, False, "b")
        self.assertTrue(txt.startswith("Salon **La Tour** clôturé"), txt)
        self.assertIn(eco.pb.fiches["b"]["cle"], txt)
        self.assertTrue(salon.supprime)
        self.assertEqual(len(archives.envois), 1)

    def test_autre_joueur_refuse_admin_accepte(self):
        eco, salon, _ = self._preparer()
        self.assertIn("Seul le joueur", self._cloturer(eco, 8, False, "La Tour"))
        self.assertFalse(salon.supprime)
        self.assertTrue(self._cloturer(eco, 8, True, "la tour").startswith("Salon "))
        self.assertTrue(salon.supprime)

    def test_sans_titre_dans_le_salon_meme(self):
        eco, salon, _ = self._preparer()
        self.assertIn("Aucun salon", self._cloturer(eco, 7, False, "", 999))
        self.assertTrue(self._cloturer(eco, 7, False, "", "2").startswith("Salon "))

    def test_autocompletion(self):
        eco, _, _ = self._preparer()
        self.assertEqual(asyncio.run(salon_mj.choix_cloture(eco, 7, False, "tour")), [{"name": "La Tour", "value": "b"}])
        self.assertEqual(asyncio.run(salon_mj.choix_cloture(eco, 8, False, "")), [])
        self.assertEqual(asyncio.run(salon_mj.choix_cloture(eco, 8, True, ""))[0]["name"], "La Tour (Fyb)")


if __name__ == "__main__":
    unittest.main()
