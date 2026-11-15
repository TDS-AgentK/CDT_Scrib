"""Tests de la Liste des RP (/proposer-rp, /je-veux-rp, gestion, synchronisation avec le site).
Lancer : python -m unittest discover -s tests
"""
import asyncio
import os
import random
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_APPLICATION_ID", "1")

from app import info, rp_demandes as rp  # noqa: E402
from scripts.register_commands import COMMANDS  # noqa: E402

MAINTENANT = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
LONGUE = ("https://cdn.discordapp.com/attachments/1285650689619525662/1438000000000000000/illustration.png"
          "?ex=67a1b2c3&is=67a06143&hm=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef&")


def _fiche(id_="rp0000000000001", **k):
    f = {"id": id_, "titre": "La crypte qui chante", "categorie": "magie", "resume": "Une crypte, des voix.",
         "statut": "proposee", "chroniqueur_pret": True, "auteur_discord_id": "42", "auteur_nom": "Kyanite",
         "message_discord": "111/222", "illustration": "", "discord_etat": ""}
    f.update(k)
    return f


class FauxPb:
    def __init__(self, fiches):
        self.fiches = {f["id"]: dict(f) for f in fiches}
        self.majs, self.supprimees, self.filtres = [], [], []

    async def lister(self, collection, filtre=None, tri=None, expand=None):
        assert collection == "rp_demandes"
        self.filtres.append(filtre)
        if filtre.startswith('statut="brouillon"'):
            limite = filtre.split('created<"')[1].rstrip('"')
            return [dict(f) for f in self.fiches.values() if f["statut"] == "brouillon" and f["created"] < limite]
        if filtre.startswith('statut="proposee" && chroniqueur_pret=true'):
            cle = filtre.split('categorie="')[1].rstrip('"') if "categorie=" in filtre else None
            return [dict(f) for f in self.fiches.values()
                    if f["statut"] == "proposee" and f["chroniqueur_pret"] and (cle is None or f["categorie"] == cle)]
        return [dict(f) for f in self.fiches.values() if f.get("message_discord") and f["statut"] != "brouillon"]

    async def maj(self, collection, id_, donnees):
        self.majs.append((id_, donnees))
        self.fiches[id_].update(donnees)
        return dict(self.fiches[id_])

    async def supprimer(self, collection, id_):
        self.supprimees.append(id_)
        self.fiches.pop(id_)


class FenetreTest(unittest.TestCase):
    def test_custom_id_sans_illustration_et_illustration_courte(self):
        cid = rp.id_fenetre("bavard", True, rp.ref_illustration(""))
        self.assertEqual(cid, "eco:rpm:bavard:1:")
        self.assertEqual(rp.lire_fenetre(cid), ("bavard", True, "", ""))
        courte = "https://i.imgur.com/abc.png"
        cid = rp.id_fenetre("wtf", False, rp.ref_illustration(courte))
        self.assertLessEqual(len(cid), 100)
        self.assertEqual(rp.lire_fenetre(cid), ("wtf", False, courte, ""))

    def test_adresse_trop_longue_passe_par_un_brouillon(self):
        self.assertIsNone(rp.ref_illustration(LONGUE))
        cid = rp.id_fenetre("decouverte", False, rp.ref_illustration(LONGUE, "abcdefghij12345"))
        self.assertLessEqual(len(cid), 100)
        self.assertEqual(rp.lire_fenetre(cid), ("decouverte", False, "", "abcdefghij12345"))

    def test_categorie_inconnue_ramenee_a_decouverte(self):
        self.assertEqual(rp.lire_fenetre("eco:rpm:???:1:")[0], "decouverte")

    def test_fenetre_trois_champs(self):
        f = rp.fenetre("magie", False, "")
        self.assertEqual(f["title"], "Proposer un RP")
        self.assertEqual([r["components"][0]["custom_id"] for r in f["components"]], ["titre", "resume", "personnage"])
        self.assertFalse(f["components"][2]["components"][0]["required"])

    def test_commande_ouvre_la_fenetre_ou_cree_un_brouillon(self):
        crees = []

        class Pb:
            async def creer(self, collection, donnees):
                crees.append(donnees)
                return {"id": "brouillon000001", **donnees}

        eco, membre = SimpleNamespace(pb=Pb()), SimpleNamespace(id=42)
        rep = asyncio.run(rp.commande_proposer(eco, membre, {"options": [{"name": "categorie", "value": "aventure"}]}))
        self.assertEqual((rep["type"], rep["data"]["custom_id"]), (9, "eco:rpm:aventure:0:"))
        self.assertEqual(crees, [])
        data = {"options": [{"name": "categorie", "value": "aventure"}, {"name": "chroniqueur-pret", "value": True},
                            {"name": "illustration", "value": "99"}],
                "resolved": {"attachments": {"99": {"url": LONGUE, "content_type": "image/png", "size": 1000}}}}
        rep = asyncio.run(rp.commande_proposer(eco, membre, data))
        self.assertEqual(rep["data"]["custom_id"], "eco:rpm:aventure:1:bbrouillon000001")
        self.assertEqual((crees[0]["statut"], crees[0]["illustration_source"]), ("brouillon", LONGUE))

    def test_piece_jointe_qui_n_est_pas_une_image(self):
        data = {"options": [{"name": "categorie", "value": "aventure"}, {"name": "illustration", "value": "9"}],
                "resolved": {"attachments": {"9": {"url": "https://x/y.pdf", "content_type": "application/pdf", "size": 10}}}}
        rep = asyncio.run(rp.commande_proposer(SimpleNamespace(pb=None), SimpleNamespace(id=1), data))
        self.assertEqual(rep["type"], 4)
        self.assertIn("image", rep["data"]["embeds"][0]["description"])


class AnnonceTest(unittest.TestCase):
    def test_annonce_proposee(self):
        e = rp.embed_annonce(_fiche(chroniqueur_pret=False, personnage_nom="Aliénor"))
        self.assertEqual(e["author"]["name"], "Kyanite propose un RP · RP maaaagic")
        self.assertEqual(e["color"], 0xB9A0F2)
        self.assertIn("La crypte qui chante", e["title"])
        self.assertEqual(e["fields"][0], {"name": "Chroniqueur", "value": "⏳ En cours de création", "inline": True})
        self.assertEqual(e["fields"][1]["value"], "Aliénor")
        self.assertEqual(e["footer"]["text"], "Aussi visible sur la Liste des RP du site")
        boutons = rp.composants_annonce(_fiche())[0]["components"]
        self.assertTrue(boutons[0]["url"].endswith("/liste-des-rp#rp0000000000001"))
        self.assertEqual(boutons[1]["custom_id"], "eco:rp:g:rp0000000000001")

    def test_annonce_annulee_grisee_barree_sans_boutons(self):
        f = _fiche(statut="annulee")
        e = rp.embed_annonce(f)
        self.assertEqual(e["color"], rp.GRIS)
        self.assertTrue(e["title"].startswith("~~"))
        self.assertIn("RP annulé", e["description"])
        self.assertEqual(rp.composants_annonce(f), [])

    def test_annonce_jouee_et_auteur_du_site(self):
        e = rp.embed_annonce(_fiche(statut="jouee", expand={"auteur": {"pseudo": "Ràlas"}}))
        self.assertTrue(e["author"]["name"].startswith("Ràlas propose un RP"))
        self.assertIn({"name": "Statut", "value": "🎲 RP joué", "inline": True}, e["fields"])

    def test_vignette_depuis_pocketbase(self):
        os.environ["PB_PUBLIC_URL"] = "https://pb.exemple"
        try:
            e = rp.embed_annonce(_fiche(illustration="illustration_ab12.png"))
        finally:
            del os.environ["PB_PUBLIC_URL"]
        self.assertEqual(e["thumbnail"]["url"], "https://pb.exemple/api/files/rp_demandes/rp0000000000001/illustration_ab12.png")

    def test_personnage_retrouve(self):
        persos = [{"id": "p1", "prenom": "Aliénor"}, {"id": "p2", "prenom": "Alix"}, {"id": "p3", "prenom": "Gaynor"}]
        self.assertEqual(rp.choisir_personnage("alienor", persos)["id"], "p1")
        self.assertEqual(rp.choisir_personnage("gay", persos)["id"], "p3")
        self.assertIsNone(rp.choisir_personnage("ali", persos))  # deux possibles : on garde le texte tapé
        self.assertIsNone(rp.choisir_personnage("", persos))


class GestionTest(unittest.TestCase):
    def test_droits(self):
        auteur = SimpleNamespace(id=42, guild_permissions=SimpleNamespace(administrator=False))
        autre = SimpleNamespace(id=7, guild_permissions=SimpleNamespace(administrator=False))
        admin = SimpleNamespace(id=8, guild_permissions=SimpleNamespace(administrator=True))
        kyanite = SimpleNamespace(id=286482667023892480, guild_permissions=SimpleNamespace(administrator=False))
        f = _fiche()
        self.assertTrue(rp.peut_gerer(auteur, f))
        self.assertFalse(rp.peut_gerer(autre, f))
        self.assertTrue(rp.peut_gerer(admin, f))
        self.assertTrue(rp.peut_gerer(kyanite, f))

    def test_boutons_de_gestion(self):
        ids = lambda f: [b["custom_id"].split(":")[-1] for b in rp.vue_gestion(f)["components"][0]["components"]]
        m = "rp0000000000001"  # bouton « Modifier » : eco:rp:m:<id>
        self.assertEqual(ids(_fiche(chroniqueur_pret=False)), [m, "pret", "jouee", "annuler"])
        self.assertEqual(ids(_fiche(chroniqueur_pret=True)), [m, "encours", "jouee", "annuler"])
        self.assertEqual(ids(_fiche(statut="jouee")), [m, "rouvrir", "annuler"])
        self.assertEqual(rp.vue_gestion(_fiche())["components"][0]["components"][0]["label"], "Modifier")
        self.assertEqual(rp.vue_gestion(_fiche())["flags"], 1 << 6)

    def test_action_refusee_a_un_autre_joueur(self):
        pb = FauxPb([_fiche()])

        async def premier(collection, filtre):
            return dict(pb.fiches["rp0000000000001"])
        pb.premier = premier
        eco = SimpleNamespace(pb=pb)
        ok, txt, _ = asyncio.run(rp.appliquer(eco, SimpleNamespace(id=7), "rp0000000000001", "annuler"))
        self.assertFalse(ok)
        self.assertEqual(pb.majs, [])


class TirageTest(unittest.TestCase):
    def test_un_autre_ne_retombe_pas_sur_le_meme(self):
        fiches = [_fiche("a" * 15), _fiche("b" * 15)]
        for graine in range(20):
            self.assertEqual(rp.tirer(fiches, "a" * 15, random.Random(graine))["id"], "b" * 15)
        self.assertEqual(rp.tirer(fiches[:1], "a" * 15)["id"], "a" * 15)  # seul RP : on le redonne
        self.assertIsNone(rp.tirer([], ""))

    def test_vue_tirage(self):
        fiches = [_fiche("a" * 15, lien_discord="https://discord.com/channels/1/111/222"), _fiche("b" * 15, categorie="bavard")]
        v = rp.vue_tirage(fiches, "", "b" * 15, random.Random(1))
        e = v["embeds"][0]
        self.assertEqual(e["author"]["name"], "Le registre a parlé · RP maaaagic")
        self.assertEqual(e["footer"]["text"], "Tiré parmi 2 RP proposés et prêts")
        self.assertEqual(e["fields"][0], {"name": "Proposé par", "value": "Kyanite", "inline": True})
        boutons = v["components"][0]["components"]
        self.assertEqual(boutons[0]["custom_id"], "eco:rp:t:-:" + "a" * 15)
        self.assertFalse(boutons[0]["disabled"])
        self.assertEqual([b["label"] for b in boutons], ["Un autre", "Voir l'annonce", "Voir sur le site"])

    def test_registre_vide(self):
        v = rp.vue_tirage([], "aventure")
        self.assertIn("/proposer-rp", v["embeds"][0]["description"])
        self.assertIn("RP aventure", v["embeds"][0]["description"])

    def test_candidats_filtre_par_categorie(self):
        pb = FauxPb([_fiche("a" * 15), _fiche("b" * 15, categorie="bavard"), _fiche("c" * 15, chroniqueur_pret=False)])
        self.assertEqual([f["id"] for f in asyncio.run(rp.candidats(pb, "bavard"))], ["b" * 15])
        self.assertEqual(len(asyncio.run(rp.candidats(pb))), 2)


class SynchroTest(unittest.TestCase):
    def test_annonce_modifiee_seulement_si_ce_qu_elle_montre_a_change(self):
        a_jour = _fiche("a" * 15)
        a_jour["discord_etat"] = rp.signature(a_jour)
        annulee = _fiche("b" * 15)
        annulee["discord_etat"] = rp.signature(annulee)
        annulee["statut"] = "annulee"  # annulée depuis le site après l'annonce
        brouillon = _fiche("c" * 15, statut="brouillon", message_discord="", created="2026-11-11 10:00:00.000Z")
        brouillon_recent = _fiche("d" * 15, statut="brouillon", message_discord="", created="2026-11-13 11:59:00.000Z")
        pb, edites = FauxPb([a_jour, annulee, brouillon, brouillon_recent]), []

        async def editer(f):
            edites.append(f["id"])
            return True

        n = asyncio.run(rp.synchroniser(pb, editer, None, MAINTENANT))
        self.assertEqual((n, edites), (1, ["b" * 15]))
        self.assertEqual(pb.fiches["b" * 15]["discord_etat"], rp.signature(pb.fiches["b" * 15]))
        self.assertEqual(pb.supprimees, ["c" * 15])
        # Tour suivant : plus rien à faire, et le filtre ne relit que les fiches modifiées depuis.
        edites.clear()
        asyncio.run(rp.synchroniser(pb, editer, MAINTENANT, MAINTENANT))
        self.assertEqual(edites, [])
        self.assertIn('updated>="2026-11-13 12:00:00.000Z"', pb.filtres[-2])

    def test_annonce_introuvable_notee_pour_ne_pas_reessayer(self):
        f = _fiche("a" * 15, statut="jouee")
        pb = FauxPb([f])

        async def editer(_f):
            return False
        self.assertEqual(asyncio.run(rp.synchroniser(pb, editer, None, MAINTENANT)), 0)
        self.assertEqual(pb.fiches["a" * 15]["discord_etat"], rp.signature(f))


class TriTest(unittest.TestCase):
    def _a_trier(self, n, auteur="42"):
        return [_fiche(f"t{i:014d}", titre=f"RP {i:02d}", categorie="", auteur_discord_id=auteur) for i in range(n)]

    def test_sans_categorie_affiche_a_choisir(self):
        e = rp.embed_annonce(_fiche(categorie=""))
        self.assertIn("Catégorie à choisir", e["author"]["name"])
        self.assertEqual(e["color"], rp.A_CHOISIR[1])

    def test_vue_tri_paginee(self):
        cinq = rp.vue_tri(self._a_trier(5))
        self.assertEqual(len(cinq["components"]), 5)  # 5 menus, pas de pages
        dix = self._a_trier(10)
        v = rp.vue_tri(dix, 1)
        self.assertEqual(len(v["components"]), 5)  # 4 menus + rangée de pages
        self.assertEqual(v["components"][0]["components"][0]["custom_id"], "eco:rp:c:t00000000000004:1")
        self.assertEqual(len(v["components"][0]["components"][0]["options"]), 6)
        self.assertEqual(rp.categorie("bagarre"), ("RP bagarr", 0xE0665A, "🥊"))
        nav = v["components"][-1]["components"]
        self.assertEqual((nav[0]["custom_id"], nav[1]["label"], nav[2]["custom_id"]), ("eco:rp:tri:0", "Page 2/3", "eco:rp:tri:2"))
        self.assertEqual(rp.vue_tri([])["embeds"][0]["title"], "Rien à trier, merci !")
        for rangee in v["components"]:
            for c in rangee["components"]:
                self.assertLessEqual(len(c.get("custom_id", "")), 100)

    def test_choix_enregistre_et_remercie(self):
        appels = []

        async def faux_discord(methode, chemin, corps=None):
            appels.append(chemin)
            return {}
        ancien, rp._discord = rp._discord, faux_discord
        self.addCleanup(setattr, rp, "_discord", ancien)
        fiches = self._a_trier(2)
        pb = FauxPb(fiches)

        async def premier(collection, filtre):
            fid = filtre.split('id="')[1].split('"')[0]
            return dict(pb.fiches[fid]) if fid in pb.fiches else None

        async def requete(methode, chemin, params=None):
            return dict(pb.fiches[chemin.rsplit("/", 1)[1]])

        async def lister(collection, filtre=None, tri=None, expand=None):
            return [dict(f) for f in pb.fiches.values() if f["categorie"] == "" and f["auteur_discord_id"] == "42"]
        pb.premier, pb.requete, pb.lister = premier, requete, lister
        eco = SimpleNamespace(pb=pb)
        auteur = SimpleNamespace(id=42, guild_permissions=SimpleNamespace(administrator=False))
        v = asyncio.run(rp.trier(eco, auteur, "t00000000000000", "aventure", 0))
        self.assertEqual(pb.fiches["t00000000000000"]["categorie"], "aventure")
        self.assertEqual(appels, ["/channels/111/messages/222"])  # annonce Discord remise à jour
        self.assertIn("Merci ! « RP 00 » → **RP aventure**. Encore 1 RP à trier.", v["embeds"][0]["description"])
        v = asyncio.run(rp.trier(eco, auteur, "t00000000000001", "magie", 0))
        self.assertEqual(v["embeds"][0]["title"], "Rien à trier, merci !")
        # Un autre joueur ne peut pas trier les RP de quelqu'un d'autre.
        pb.fiches["t00000000000001"]["categorie"] = ""
        autre = SimpleNamespace(id=7, guild_permissions=SimpleNamespace(administrator=False))
        v = asyncio.run(rp.trier(eco, autre, "t00000000000001", "wtf", 0))
        self.assertEqual(pb.fiches["t00000000000001"]["categorie"], "")

    def test_gerer_contient_le_menu_categorie(self):
        v = rp.vue_gestion(_fiche(categorie=""))
        menu = v["components"][-1]["components"][0]
        self.assertEqual((menu["type"], menu["custom_id"], menu["placeholder"]), (3, "eco:rp:s:rp0000000000001", "Catégorie"))


class PbModif:
    """Base factice pour « Modifier » : fiches rp_demandes (avec expand), personnages, envois de fichiers."""
    def __init__(self, fiches, joueurs=(), persos=()):
        self.fiches = {f["id"]: dict(f) for f in fiches}
        self.joueurs = {j["id"]: j for j in joueurs}
        self.persos = list(persos)
        self.majs, self.fichiers, self.filtres = [], [], []

    async def premier(self, collection, filtre):
        fid = filtre.split('id="')[1].split('"')[0]
        return dict(self.fiches[fid]) if fid in self.fiches and self.fiches[fid]["statut"] != "brouillon" else None

    async def requete(self, methode, chemin, params=None):
        f = dict(self.fiches[chemin.rsplit("/", 1)[1]])
        f["expand"] = {k: v for k, v in (("auteur", self.joueurs.get(f.get("auteur"))),
                                          ("personnage", next((x for x in self.persos if x["id"] == f.get("personnage")), None))) if v}
        return f

    async def maj(self, collection, id_, donnees):
        self.majs.append((id_, donnees))
        self.fiches[id_].update(donnees)
        return dict(self.fiches[id_])

    async def lister(self, collection, filtre=None, tri=None, expand=None):
        self.filtres.append((collection, filtre))
        if collection == "personnages":
            pseudo = filtre.split('joueur="')[1].rstrip('"')
            return [p for p in self.persos if p["joueur"] == pseudo]
        return [dict(f) for f in self.fiches.values() if f["statut"] not in ("annulee", "brouillon")]

    async def envoyer_fichier(self, collection, id_, champ, nom, contenu, type_mime):
        self.fichiers.append((id_, nom, type_mime))
        self.fiches[id_][champ] = nom
        return dict(self.fiches[id_])


def _membre(id_, admin=False):
    return SimpleNamespace(id=id_, guild_permissions=SimpleNamespace(administrator=admin))


class ModifierTest(unittest.TestCase):
    def setUp(self):
        self.annonces = []

        async def faux_discord(methode, chemin, corps=None):
            self.annonces.append((methode, chemin, corps))
            return {}
        ancien, rp._discord = rp._discord, faux_discord
        self.addCleanup(setattr, rp, "_discord", ancien)
        # RP importé : auteur relié à la fiche joueur de Kyanite, mais sans ID Discord noté sur le RP.
        self.importe = _fiche("imp000000000001", titre="La Bête", resume="Une fête de village…", categorie="",
                              auteur="jKy", auteur_discord_id="", auteur_nom="Kyanite", message_discord="")
        self.annonce = _fiche()  # proposé sur Discord par l'ID 42, annonce 111/222
        self.pb = PbModif([self.importe, self.annonce], joueurs=[{"id": "jKy", "pseudo": "Kyanite"}],
                          persos=[{"id": "pAli", "prenom": "Aliénor", "joueur": "Kyanite"}])

        async def joueur_de(m):
            return {"id": "jKy", "pseudo": "Kyanite"} if m.id == 286482667023892480 else None
        self.eco = SimpleNamespace(pb=self.pb, joueur_de=joueur_de)

    def soumettre(self, membre, custom_id, titre, resume="", perso=""):
        payload = {"data": {"custom_id": custom_id, "components": [
            {"type": 1, "components": [{"custom_id": "titre", "value": titre}]},
            {"type": 1, "components": [{"custom_id": "resume", "value": resume}]},
            {"type": 1, "components": [{"custom_id": "personnage", "value": perso}]}]}}
        return asyncio.run(rp.envoyer_modif(self.eco, membre, payload))

    def test_autorisations(self):
        ky, auteur42 = _membre(286482667023892480), _membre(42)
        autre, admin = _membre(7), _membre(8, admin=True)
        a = lambda m, f: asyncio.run(rp.autorise(self.eco, m, f))
        self.assertTrue(a(ky, self.importe))       # auteur par sa fiche joueur
        self.assertTrue(a(auteur42, self.annonce))  # auteur par son ID Discord
        self.assertFalse(a(autre, self.importe))   # autre joueur : non
        self.assertFalse(a(autre, self.annonce))
        self.assertTrue(a(admin, self.importe))    # admin : oui

    def test_fenetre_preremplie(self):
        rep_ = asyncio.run(rp.ouvrir_modif(self.eco, _membre(42), "rp0000000000001", "bavard", True))
        self.assertEqual(rep_["type"], 9)
        f = rep_["data"]
        self.assertEqual(f["custom_id"], "eco:rpe:rp0000000000001:bavard:1:")
        valeurs = {r["components"][0]["custom_id"]: r["components"][0].get("value") for r in f["components"]}
        self.assertEqual(valeurs, {"titre": "La crypte qui chante", "resume": "Une crypte, des voix.", "personnage": None})
        refus = asyncio.run(rp.ouvrir_modif(self.eco, _membre(7), "rp0000000000001"))
        self.assertEqual(refus["type"], 4)
        self.assertEqual(self.pb.majs, [])

    def test_illustration_longue_gardee_sur_la_fiche(self):
        rep_ = asyncio.run(rp.ouvrir_modif(self.eco, _membre(42), "rp0000000000001", url=LONGUE))
        self.assertTrue(rep_["data"]["custom_id"].endswith(":-:-:f"))
        self.assertEqual(self.pb.fiches["rp0000000000001"]["illustration_source"], LONGUE)
        self.assertEqual(rp.lire_modif("eco:rpe:abc:magie:0:uhttps://x/y.png"), ("abc", "magie", False, "uhttps://x/y.png"))
        self.assertEqual(rp.lire_modif("eco:rpe:abc:-:-:"), ("abc", None, None, ""))

    def test_envoi_met_a_jour_fiche_et_annonce(self):
        async def telecharger(url):
            return (b"png", "image/png")
        ancien, rp._telecharger = rp._telecharger, telecharger
        self.addCleanup(setattr, rp, "_telecharger", ancien)
        ok, txt = self.soumettre(_membre(42), "eco:rpe:rp0000000000001:bagarre:0:uhttps://x/y.png", "La crypte qui hurle", "Plus fort.", "Odile")
        self.assertTrue(ok, txt)
        f = self.pb.fiches["rp0000000000001"]
        self.assertEqual((f["titre"], f["resume"], f["categorie"], f["chroniqueur_pret"], f["personnage_nom"]),
                         ("La crypte qui hurle", "Plus fort.", "bagarre", False, "Odile"))
        self.assertEqual(self.pb.fichiers, [("rp0000000000001", "illustration.png", "image/png")])
        self.assertEqual(self.annonces[-1][:2], ("PATCH", "/channels/111/messages/222"))  # annonce remise à jour
        self.assertEqual(f["discord_etat"], rp.signature(asyncio.run(self.pb.requete("GET", "/x/rp0000000000001"))))

    def test_rp_importe_modifie_par_son_auteur_sans_annonce(self):
        ok, _ = self.soumettre(_membre(286482667023892480), "eco:rpe:imp000000000001:magie:1:", "La Bête", "Une fête…", "alienor")
        self.assertTrue(ok)
        f = self.pb.fiches["imp000000000001"]
        self.assertEqual((f["categorie"], f["chroniqueur_pret"], f["personnage"], f["personnage_nom"]), ("magie", True, "pAli", ""))
        self.assertEqual(self.annonces, [])  # pas d'annonce Discord pour un RP importé

    def test_envoi_refuse_a_un_autre_joueur(self):
        ok, txt = self.soumettre(_membre(7), "eco:rpe:imp000000000001:-:-:", "Piraté")
        self.assertFalse(ok)
        self.assertEqual(self.pb.fiches["imp000000000001"]["titre"], "La Bête")

    def test_autocompletion_limitee_a_ses_rp(self):
        async def joueur_de(m):
            return {"id": "jRa", "pseudo": "Ràlas"} if m.id == 333 else None
        self.eco.joueur_de = joueur_de
        miens = asyncio.run(rp.choix_modifier(self.eco, _membre(333), ""))
        filtre = self.pb.filtres[-1][1]
        self.assertIn('auteur_discord_id="333"', filtre)  # ses RP proposés sur Discord…
        self.assertIn('auteur="jRa"', filtre)  # … et ceux reliés à sa fiche joueur (RP importés)
        self.assertTrue(all(len(c["name"]) <= 100 for c in miens))
        asyncio.run(rp.choix_modifier(self.eco, _membre(8, admin=True), "bête"))
        self.assertEqual(self.pb.filtres[-1][1], 'statut!="annulee" && statut!="brouillon"')  # admin : tous


class CommandesTest(unittest.TestCase):
    def test_commandes_declarees_et_dans_info(self):
        par_nom = {c["name"]: c for c in COMMANDS}
        p = par_nom["proposer-rp"]
        self.assertEqual([o["name"] for o in p["options"]], ["categorie", "chroniqueur-pret", "illustration"])
        self.assertEqual([o["type"] for o in p["options"]], [3, 5, 11])
        self.assertEqual({c["value"] for c in p["options"][0]["choices"]}, set(rp.CATEGORIES))
        self.assertFalse(par_nom["je-veux-rp"]["options"][0]["required"])
        texte = str(info.vue_info())
        self.assertIn("/proposer-rp", texte)
        self.assertIn("/je-veux-rp", texte)
        self.assertIn("/modifier-rp", texte)
        # /rp-a-trier retirée : plus déclarée, et effacée de Discord au démarrage (COMMANDES_RETIREES).
        self.assertNotIn("rp-a-trier", par_nom)
        self.assertNotIn("rp-a-trier", texte)
        from scripts.register_commands import COMMANDES_RETIREES
        self.assertIn("rp-a-trier", COMMANDES_RETIREES)
        m = par_nom["modifier-rp"]
        self.assertEqual([o["name"] for o in m["options"]], ["rp", "categorie", "chroniqueur-pret", "illustration"])
        self.assertTrue(m["options"][0]["autocomplete"])


if __name__ == "__main__":
    unittest.main()
