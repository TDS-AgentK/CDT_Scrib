"""Fiches créées sur le site : embeds, envoi, modification d'un envoi, effacement 24 h, /fiche voir et pelage.
Lancer : python -m unittest discover -s tests
"""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from app import fiches_site
from app.database import Database
from app.embeds import build_character_embed
from app.discord_types import ResponseType
from app.handlers import handle_autocomplete, handle_command, handle_modal_submit

T0 = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)

FICHE = {
    "id": "f1", "nom_id": "amokualnesi", "couleur": 0x3BA55C, "nom": "Amokualnesi", "surnom": "Laideur",
    "genre": "♀", "race": "Hybride", "taille": "181 cm", "couleur_cheveux": "Noirs et blancs", "coiffure": "Une pince",
    "tenue": "Grande robe noire", "nom_skin": "Amo.png", "image_personnage_url": "https://x/portrait.png",
    "illustrations": [{"titre": "", "url": "https://x/tenue.png"}, {"titre": "Dague", "url": "https://x/dague.png"}],
}


def _run(coro):
    return asyncio.run(coro)


class FauxPB:
    def __init__(self, envois=(), fiches=()):
        self.envois = {e["id"]: dict(e) for e in envois}
        self.fiches = list(fiches)
        self.maj_faites = []

    async def maj(self, collection, id_, donnees):
        self.maj_faites.append((collection, id_, donnees))
        self.envois.get(id_, {}).update(donnees)
        return donnees

    async def lister(self, collection, filtre=None, tri=None, expand=None):
        if collection == fiches_site.COL_ENVOIS:
            return [dict(e, expand={"fiche": FICHE}) for e in self.envois.values()
                    if e["statut"] in ("en_attente", "a_modifier", "a_supprimer")
                    or (e["statut"] == "envoye" and e.get("expire_le", "") <= fiches_site._pb_date(T0 + timedelta(days=2)))]
        return [f for f in self.fiches if not filtre or f["nom_id"].startswith(filtre.split('"')[1].rstrip("%"))]

    async def premier(self, collection, filtre):
        nom = filtre.split('"')[1]
        return next((f for f in self.fiches if f["nom_id"] == nom), None)


class FauxDiscord:
    def __init__(self):
        self.messages = {}
        self.n = 0

    async def poster(self, salon, embeds):
        self.n += 1
        self.messages[str(self.n)] = embeds
        return str(self.n)

    async def editer(self, salon, message_id, embeds):
        if message_id not in self.messages:
            return False
        self.messages[message_id] = embeds
        return True

    async def effacer(self, salon, message_id):
        self.messages.pop(message_id, None)


class EmbedsTest(unittest.TestCase):
    def test_meme_mise_en_page_que_fiche_voir(self):
        principal, illustrations = fiches_site.messages_de(FICHE)
        self.assertEqual(principal["title"], "Amokualnesi")
        self.assertEqual(principal["description"], "Laideur")
        self.assertEqual(principal["thumbnail"]["url"], "https://x/portrait.png")
        self.assertEqual(principal["footer"]["text"], "Amokualnesi, Laideur")
        self.assertIn("Amo.png", principal["author"]["icon_url"])
        noms = [c["name"] for c in principal["fields"]]
        self.assertEqual(noms[:3], ["Sexe", "Race", "Taille"])
        self.assertIn("Coiffure", noms)
        # Mosaïque : un seul bloc (même url), titre et couleur sur le premier embed, titres des images en description.
        self.assertEqual([e.get("title") for e in illustrations], ["Illustrations", None])
        self.assertEqual(illustrations[0]["description"], "Dague")
        self.assertEqual(illustrations[0]["color"], 0x3BA55C)
        self.assertEqual(set(illustrations[1]), {"url", "image"})
        self.assertEqual(len({e["url"] for e in illustrations}), 1)

    def test_mosaique_par_groupes_de_quatre(self):
        for n, groupes in ((1, [1]), (2, [2]), (4, [4]), (5, [4, 1]), (10, [4, 4, 2]), (12, [4, 4, 2])):
            images = [{"titre": "", "url": f"https://x/{i}.png"} for i in range(n)]
            embeds = fiches_site.messages_de(dict(FICHE, illustrations=images))[1]
            with self.subTest(n=n):
                self.assertEqual(len(embeds), min(n, 10))
                urls = [e["url"] for e in embeds]
                self.assertEqual([urls.count(u) for u in dict.fromkeys(urls)], groupes)
                self.assertTrue(all(u.startswith("https://") and u.endswith(f"#illus-{k + 1}") for k, u in enumerate(dict.fromkeys(urls))))
                self.assertEqual([e["image"]["url"] for e in embeds], [i["url"] for i in images[:10]])
                premiers = [k for k, e in enumerate(embeds) if k == 0 or e["url"] != embeds[k - 1]["url"]]
                self.assertEqual([embeds[k].get("color") for k in premiers], [0x3BA55C] * len(groupes))
                self.assertEqual(embeds[0]["title"], "Illustrations")
                self.assertTrue(all(set(e) == {"url", "image"} for k, e in enumerate(embeds) if k not in premiers))
                self.assertNotIn("description", embeds[0])

    def test_pelage_remplace_cheveux_et_coiffure(self):
        principal, _ = fiches_site.messages_de(dict(FICHE, couleur_pelage="Noir de jais"))
        noms = [c["name"] for c in principal["fields"]]
        self.assertIn("Couleur du pelage", noms)
        self.assertNotIn("Coiffure", noms)
        self.assertNotIn("Couleur et longueur des cheveux", noms)


class FileTest(unittest.TestCase):
    def test_envoi_modification_puis_effacement_a_24h(self):
        async def scenario():
            d = FauxDiscord()
            pb = FauxPB([{"id": "e1", "fiche": "f1", "salon_id": "123", "statut": "en_attente",
                          "created": fiches_site._pb_date(T0)}])
            await fiches_site.tour(pb, d.poster, d.editer, d.effacer, T0)
            e = pb.envois["e1"]
            self.assertEqual(e["statut"], "envoye")
            self.assertEqual(len(e["messages"]), 2)
            self.assertEqual(e["expire_le"], fiches_site._pb_date(T0 + timedelta(hours=24)))

            # Modification : mêmes messages, même heure d'effacement ; plus d'illustrations -> second message effacé.
            global FICHE
            ancienne = FICHE
            FICHE = dict(ancienne, surnom="La Belle", illustrations=[])
            e["statut"] = "a_modifier"
            await fiches_site.tour(pb, d.poster, d.editer, d.effacer, T0 + timedelta(hours=3))
            self.assertEqual(e["statut"], "envoye")
            self.assertEqual(e["expire_le"], fiches_site._pb_date(T0 + timedelta(hours=24)))
            self.assertEqual(d.messages[e["messages"][0]["id"]][0]["description"], "La Belle")
            self.assertEqual(len(d.messages), 1)
            FICHE = ancienne

            await fiches_site.tour(pb, d.poster, d.editer, d.effacer, T0 + timedelta(hours=23))
            self.assertEqual(e["statut"], "envoye")
            await fiches_site.tour(pb, d.poster, d.editer, d.effacer, T0 + timedelta(hours=24))
            self.assertEqual(e["statut"], "supprime")
            self.assertEqual(d.messages, {})

        _run(scenario())

    def test_salon_absent_note_une_erreur(self):
        async def scenario():
            d = FauxDiscord()
            pb = FauxPB([{"id": "e1", "fiche": "f1", "salon_id": "", "statut": "en_attente", "created": fiches_site._pb_date(T0)}])
            await fiches_site.tour(pb, d.poster, d.editer, d.effacer, T0)
            self.assertEqual(pb.envois["e1"]["statut"], "erreur")
            self.assertIn("salon", pb.envois["e1"]["erreur"])

        _run(scenario())


class CommandesTest(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.dossier.name, "fiches.db"))
        self.site = fiches_site.FichesSite(FauxPB(fiches=[FICHE]))

    def tearDown(self):
        _run(self.db.close())
        self.dossier.cleanup()

    def _cmd(self, sub, nom):
        data = {"options": [{"name": sub, "options": [{"name": "nom", "value": nom}]}]}
        return _run(handle_command(self.db, data, {"id": "42"}, self.site))

    def test_voir_une_fiche_du_site(self):
        reponse, suite = self._cmd("voir", "amokualnesi")
        self.assertEqual(reponse["data"]["embeds"][0]["title"], "Amokualnesi")
        self.assertEqual(len(suite), 2)

    def test_creer_refuse_un_nom_du_site(self):
        reponse, _ = self._cmd("creer", "amokualnesi")
        self.assertIn("existe déjà", reponse["data"]["content"])

    def test_pelage_sur_une_fiche_du_bot(self):
        async def scenario():
            await self.db.create("loup", 42)
            await self.db.update_section("loup", {"nom": "Loup", "couleur_cheveux": "Gris", "couleur_pelage": "Gris cendre"})
            return build_character_embed(await self.db.get("loup"))
        noms = [c["name"] for c in _run(scenario())["fields"]]
        self.assertEqual(noms, ["Couleur du pelage"])


class PBModifiable:
    """Base du site : fiches, envois, comptes (users → joueurs.discord_id) et personnages."""

    def __init__(self, fiches, envois=(), comptes=(), echoue=False):
        self.fiches = {f["id"]: dict(f) for f in fiches}
        self.envois = {e["id"]: dict(e) for e in envois}
        self.comptes = list(comptes)
        self.echoue = echoue

    async def premier(self, collection, filtre):
        nom = filtre.split('"')[1]
        return next((dict(f) for f in self.fiches.values() if f["nom_id"] == nom), None)

    async def lister(self, collection, filtre=None, tri=None, expand=None):
        if collection == "users":
            did = filtre.split('"')[1]
            return [c for c in self.comptes if c["discord_id"] == did]
        if collection == fiches_site.COL_ENVOIS:
            fid = filtre.split('"')[1]
            return [dict(e) for e in self.envois.values() if e["fiche"] == fid
                    and ('statut="envoye"' not in filtre or e["statut"] == "envoye" or "||" in filtre)]
        return [dict(f) for f in self.fiches.values()
                if not filtre or "personnage.joueur" not in filtre or f'"{f["joueur"]}"' in filtre]

    async def requete(self, methode, chemin, json=None, params=None):
        return {"joueur": next(f["joueur"] for f in self.fiches.values() if f["personnage"] == chemin.rsplit("/", 1)[1])}

    async def maj(self, collection, id_, donnees):
        if self.echoue and collection == fiches_site.COL_FICHES:
            raise RuntimeError("base indisponible")
        cible = (self.fiches if collection == fiches_site.COL_FICHES else self.envois)[id_]
        cible.update(donnees)
        return dict(cible)

    async def supprimer(self, collection, id_):
        self.fiches.pop(id_)


AMO = dict(FICHE, personnage="p1", joueur="Kelora", nature=True)
AMO_ARMURE = dict(FICHE, id="f2", nom_id="amokualnesi-armure", personnage="p1", joueur="Kelora", nature=False)
COMPTES = [{"discord_id": "42", "name": "Kelora", "role": "joueur"}, {"discord_id": "7", "name": "Agent K", "role": "super_admin"}]


def _fenetre(nom, section, valeurs):
    return {"custom_id": f"fiche_site:{nom}:{section}",
            "components": [{"components": [{"custom_id": k, "value": v}]} for k, v in valeurs.items()]}


class ModifierSurDiscordTest(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.dossier.name, "fiches.db"))

    def tearDown(self):
        _run(self.db.close())
        self.dossier.cleanup()

    def _site(self, **kw):
        self.pb = PBModifiable([AMO, AMO_ARMURE], comptes=COMPTES, **kw)
        return fiches_site.FichesSite(self.pb)

    def _cmd(self, site, sub, nom, user="42", **opts):
        options = [{"name": "nom", "value": nom}] + [{"name": k, "value": v} for k, v in opts.items()]
        return _run(handle_command(self.db, {"options": [{"name": sub, "options": options}]}, {"id": user}, site))[0]

    def test_proprietaire_et_admin_ouvrent_la_fenetre_pre_remplie(self):
        site = self._site()
        for user in ("42", "7"):
            reponse = self._cmd(site, "modifier", "amokualnesi", user, section="architecture")
            self.assertEqual(reponse["type"], ResponseType.MODAL)
            valeurs = {c["components"][0]["custom_id"]: c["components"][0].get("value", "") for c in reponse["data"]["components"]}
            self.assertEqual(valeurs["couleur"], "3BA55C")
            self.assertEqual(valeurs["equipement_image_url"], "https://x/tenue.png")
        self.assertIn("ne vous appartient pas", self._cmd(site, "modifier", "amokualnesi", "99", section="identite")["data"]["content"])

    def test_enregistrement_ecrit_sur_le_site_et_met_a_jour_les_messages(self):
        site = self._site()
        self.pb.envois = {"e1": {"id": "e1", "fiche": "f1", "statut": "envoye"}, "e2": {"id": "e2", "fiche": "f1", "statut": "supprime"}}
        reponse, _ = _run(handle_modal_submit(self.db, _fenetre("amokualnesi", "identite", {"nom": "Amo", "surnom": "", "genre": "♀", "race": "Hybride"}), {"id": "42"}, site))
        self.assertEqual(reponse["data"]["embeds"][0]["title"], "Amo")
        self.assertEqual(self.pb.fiches["f1"]["nom"], "Amo")
        self.assertEqual(self.pb.fiches["f1"]["surnom"], "")
        self.assertEqual(self.pb.fiches["f1"]["taille"], "181 cm")  # champs hors fenêtre intacts
        self.assertEqual(self.pb.envois["e1"]["statut"], "a_modifier")
        self.assertEqual(self.pb.envois["e2"]["statut"], "supprime")

    def test_valeur_illisible_ou_echec_garde_la_version_du_site(self):
        site = self._site()
        reponse, _ = _run(handle_modal_submit(self.db, _fenetre("amokualnesi", "architecture", {"couleur": "vert", "nom_skin": "B.png"}), {"id": "42"}, site))
        self.assertIn("celle du site est gardée", reponse["data"]["content"])
        self.assertEqual(self.pb.fiches["f1"]["couleur"], 0x3BA55C)
        self.assertEqual(self.pb.fiches["f1"]["nom_skin"], "B.png")

        site = self._site(echoue=True)
        reponse, _ = _run(handle_modal_submit(self.db, _fenetre("amokualnesi", "identite", {"nom": "Autre"}), {"id": "42"}, site))
        self.assertIn("version du site est conservée", reponse["data"]["content"])
        self.assertEqual(self.pb.fiches["f1"]["nom"], "Amokualnesi")

    def test_image_equipement_remplace_la_premiere_illustration(self):
        site = self._site()
        _run(site.image(self.pb.fiches["f1"], "equipement_image_url", "https://bot/images/a.png"))
        self.assertEqual([i["url"] for i in self.pb.fiches["f1"]["illustrations"]], ["https://bot/images/a.png", "https://x/dague.png"])
        _run(site.image(self.pb.fiches["f1"], "image_personnage_url", "https://bot/images/p.png"))
        self.assertEqual(self.pb.fiches["f1"]["image_personnage_url"], "https://bot/images/p.png")

    def test_supprimer_une_apparence_mais_pas_la_nature(self):
        site = self._site()
        self.pb.envois = {"e1": {"id": "e1", "fiche": "f2", "statut": "envoye", "messages": [{"id": "1"}]}}
        self.assertIn("Nature", self._cmd(site, "supprimer", "amokualnesi")["data"]["content"])
        self.assertIn("f1", self.pb.fiches)
        self._cmd(site, "supprimer", "amokualnesi-armure")
        self.assertNotIn("f2", self.pb.fiches)
        self.assertEqual(self.pb.envois["e1"]["statut"], "a_supprimer")

    def test_autocompletion_propose_les_fiches_du_site_modifiables(self):
        site = self._site()
        data = {"options": [{"name": "modifier", "options": [{"name": "nom", "value": "amo", "focused": True}]}]}
        noms = [c["value"] for c in _run(handle_autocomplete(self.db, data, {"id": "42"}, site))["data"]["choices"]]
        self.assertEqual(noms, ["amokualnesi", "amokualnesi-armure"])
        self.assertEqual(_run(handle_autocomplete(self.db, data, {"id": "99"}, site))["data"]["choices"], [])

    def test_envoi_dont_la_fiche_a_ete_supprimee_est_efface(self):
        d = FauxDiscord()
        d.messages = {"5": []}
        pb = FauxPB([{"id": "e1", "fiche": "", "statut": "a_modifier", "messages": [{"salon": "1", "id": "5", "role": "fiche"}],
                      "expire_le": fiches_site._pb_date(T0 + timedelta(hours=5))}])
        _run(fiches_site.traiter(pb.envois["e1"], pb, d.poster, d.editer, d.effacer, T0))
        self.assertEqual(pb.envois["e1"]["statut"], "supprime")
        self.assertEqual(d.messages, {})


if __name__ == "__main__":
    unittest.main()
