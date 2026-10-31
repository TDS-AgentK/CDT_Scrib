"""Tests de la boutique d'Halloween (app/halloween.py) : achat en bonbons, objets mystère, rôles (rien n'est prélevé
si le rôle ne peut pas être donné), limites, gérants et vues Discord.
Lancer : python -m unittest discover -s tests
"""
import asyncio
import random
import re
import unittest
from types import SimpleNamespace

from app import halloween

REGLAGES = {"id": "reg", "cle": "general", "ouverte": True, "titre": "Boutique d'Halloween", "monnaie_nom": "Bonbons",
            "emoji_citrouille": "🎃", "emoji_fantome": "👻", "emoji_bonbon": "🍬",
            "admin_discord_ids": "42", "annonce_hasard": True}


class FauxPb:
    """Base en mémoire : filtres « champ="valeur" » et « champ=true » respectés, le reste ignoré."""

    def __init__(self, donnees):
        self.d = donnees
        self.n = 0

    @staticmethod
    def _ok(rec, filtre):
        for champ, val in re.findall(r'(\w+)="([^"]*)"', filtre or ""):
            if str(rec.get(champ, "")) != val:
                return False
        for champ in re.findall(r"(\w+)=true", filtre or ""):
            if not rec.get(champ):
                return False
        return True

    async def lister(self, collection, filtre=None, tri=None, expand=None):
        return [r for r in self.d.get(collection, []) if self._ok(r, filtre)]

    async def premier(self, collection, filtre=None):
        return next(iter(await self.lister(collection, filtre)), None)

    async def maj(self, collection, rid, champs):
        rec = next(r for r in self.d[collection] if r["id"] == rid)
        rec.update(champs)
        return rec

    async def creer(self, collection, champs):
        self.n += 1
        rec = {"id": f"x{self.n}", **champs}
        self.d.setdefault(collection, []).append(rec)
        return rec

    async def requete(self, methode, chemin):
        parties = chemin.strip("/").split("/")
        collection, rid = parties[2], parties[-1]
        return next(r for r in self.d[collection] if r["id"] == rid)


class FauxEco:
    def __init__(self, pb, roles_existants=("Citrouille", "Fantôme")):
        self.pb, self.roles_donnes, self.objets_donnes = pb, [], []
        self.roles_existants = roles_existants
        self._verrous = {}

    async def config(self):
        return {"reglages": {}, "salons": [{"id": "s1", "salon_nom": "rp"}]}

    def _salon(self, cfg, channel):
        return next((s for s in cfg["salons"] if s["salon_nom"] == channel.name), None)

    def _verrou(self, uid):
        return self._verrous.setdefault(uid, asyncio.Lock())

    def _role_discord(self, guild, role_id, nom):
        return next((r for r in guild.roles if r.name == nom), None)

    async def joueur_de(self, membre):
        return self.pb.d["joueurs"][0]

    async def donner_role(self, membre, joueur, role_id, nom, jours, origine=""):
        if nom not in self.roles_existants:
            return f"le rôle « {nom} » n'existe pas sur ce serveur"
        self.roles_donnes.append((nom, jours))

    async def ajouter_objet(self, joueur_id, objet_id, q, motif, note=""):
        self.objets_donnes.append((objet_id, q))


def base(bonbons=50, articles=(), lots=()):
    return {
        "hal_reglages": [dict(REGLAGES)],
        "joueurs": [{"id": "j1", "pseudo": "Ràlas", "discord_id": "7", "eco_or": 0}],
        "hal_soldes": [{"id": "s1", "joueur": "j1", "bonbons": bonbons, "total_gagne": bonbons}],
        "hal_articles": [dict(a) for a in articles], "hal_lots": [dict(l) for l in lots], "hal_mouvements": [],
        "eco_objets": [{"id": "o1", "nom": "Masque", "emoji": "🎭"}], "eco_gains": [],
    }


def membre(roles=(), uid=7, admin=False):
    role_objs = [SimpleNamespace(name=r, id=i) for i, r in enumerate(roles)]
    guild = SimpleNamespace(roles=role_objs + [SimpleNamespace(name="Citrouille", id=90), SimpleNamespace(name="Fantôme", id=91)])
    return SimpleNamespace(id=uid, roles=role_objs, guild=guild, mention=f"<@{uid}>", name="ralas", display_name="Ràlas",
                           guild_permissions=SimpleNamespace(administrator=admin))


ROLE = {"id": "a_role", "nom": "Rôle Citrouille", "type": "role", "role_nom": "Citrouille", "prix": 20, "actif": True, "stock_illimite": True}
OBJET = {"id": "a_obj", "nom": "Masque", "type": "objet", "objet": "o1", "quantite": 2, "prix": 10, "actif": True, "stock": 1}
MYSTERE = {"id": "a_mys", "nom": "Citrouille mystère", "type": "hasard", "prix": 5, "actif": True, "stock_illimite": True}


def run(coro):
    halloween.oublier_cache()
    return asyncio.run(coro)


class Achat(unittest.TestCase):
    def test_role_achete_et_debite(self):
        pb = FauxPb(base(50, [ROLE]))
        eco = FauxEco(pb)
        ok, txt, annonce = run(halloween.acheter(eco, membre(), "a_role"))
        self.assertTrue(ok, txt)
        self.assertEqual(eco.roles_donnes, [("Citrouille", 0)])
        self.assertEqual(pb.d["hal_soldes"][0]["bonbons"], 30)
        self.assertEqual(pb.d["hal_mouvements"][-1]["type"], "achat")
        self.assertEqual(annonce, "")

    def test_role_impossible_rien_preleve(self):
        pb = FauxPb(base(50, [{**ROLE, "role_nom": "Inconnu"}]))
        ok, txt, _ = run(halloween.acheter(FauxEco(pb), membre(), "a_role"))
        self.assertFalse(ok)
        self.assertIn("rien n'a été prélevé", txt)
        self.assertEqual(pb.d["hal_soldes"][0]["bonbons"], 50)

    def test_role_deja_detenu(self):
        pb = FauxPb(base(50, [ROLE]))
        ok, txt, _ = run(halloween.acheter(FauxEco(pb), membre(roles=["Citrouille"]), "a_role"))
        self.assertFalse(ok)
        self.assertEqual(pb.d["hal_soldes"][0]["bonbons"], 50)

    def test_pas_assez_de_bonbons(self):
        pb = FauxPb(base(5, [ROLE]))
        ok, txt, _ = run(halloween.acheter(FauxEco(pb), membre(), "a_role"))
        self.assertFalse(ok)
        self.assertIn("Il te manque", txt)

    def test_boutique_fermee(self):
        pb = FauxPb(base(50, [ROLE]))
        pb.d["hal_reglages"][0]["ouverte"] = False
        ok, txt, _ = run(halloween.acheter(FauxEco(pb), membre(), "a_role"))
        self.assertFalse(ok)
        self.assertIn("fermée", txt)

    def test_objet_et_stock(self):
        pb = FauxPb(base(50, [OBJET]))
        eco = FauxEco(pb)
        ok, txt, _ = run(halloween.acheter(eco, membre(), "a_obj"))
        self.assertTrue(ok, txt)
        self.assertEqual(eco.objets_donnes, [("o1", 2)])
        self.assertEqual(pb.d["hal_articles"][0]["stock"], 0)
        ok, txt, _ = run(halloween.acheter(eco, membre(), "a_obj"))
        self.assertFalse(ok)
        self.assertIn("épuisé", txt)

    def test_limite_par_joueur(self):
        pb = FauxPb(base(50, [{**OBJET, "stock_illimite": True, "limite_par_joueur": 1}]))
        eco = FauxEco(pb)
        self.assertTrue(run(halloween.acheter(eco, membre(), "a_obj"))[0])
        ok, txt, _ = run(halloween.acheter(eco, membre(), "a_obj"))
        self.assertFalse(ok)
        self.assertIn("nombre de fois permis", txt)


class Mystere(unittest.TestCase):
    LOTS = [
        {"id": "l1", "article": "a_mys", "libelle": "Bonbons", "type": "bonbons", "quantite": 12, "poids": 1, "actif": True},
        {"id": "l2", "article": "a_mys", "libelle": "Rôle Fantôme", "type": "role", "role_nom": "Fantôme", "poids": 1, "actif": True},
        {"id": "l3", "article": "a_mys", "libelle": "Rien", "type": "rien", "poids": 0, "actif": True},
    ]

    def test_tirage_pondere(self):
        rng = random.Random(1)
        tirages = {halloween.tirer(self.LOTS, rng)["id"] for _ in range(200)}
        self.assertEqual(tirages, {"l1", "l2"})  # poids 0 : jamais tiré
        self.assertIsNone(halloween.tirer([], rng))

    def test_lot_bonbons_et_annonce(self):
        pb = FauxPb(base(10, [MYSTERE], [self.LOTS[0]]))
        ok, txt, annonce = run(halloween.acheter(FauxEco(pb), membre(), "a_mys"))
        self.assertTrue(ok, txt)
        self.assertEqual(pb.d["hal_soldes"][0]["bonbons"], 10 + 12 - 5)
        self.assertIn("<@7>", annonce)
        self.assertIn("12 🍬 Bonbons", annonce)

    def test_role_deja_detenu_jamais_tire(self):
        pb = FauxPb(base(10, [MYSTERE], self.LOTS[:2]))
        eco = FauxEco(pb)
        for _ in range(10):
            ok, txt, _ = run(halloween.acheter(eco, membre(roles=["Fantôme"]), "a_mys"))
            self.assertTrue(ok, txt)
            self.assertNotIn("Fantôme", txt)
            pb.d["hal_soldes"][0]["bonbons"] = 10

    def test_mystere_vide(self):
        pb = FauxPb(base(10, [MYSTERE]))
        ok, txt, _ = run(halloween.acheter(FauxEco(pb), membre(), "a_mys"))
        self.assertFalse(ok)
        self.assertEqual(pb.d["hal_soldes"][0]["bonbons"], 10)


class Gerants(unittest.TestCase):
    def test_donner_retirer(self):
        pb = FauxPb(base(5))
        eco = FauxEco(pb)
        ok, txt = run(halloween.donner(eco, membre(uid=42), membre(), 10, "concours"))
        self.assertTrue(ok, txt)
        self.assertEqual(pb.d["hal_soldes"][0]["bonbons"], 15)
        ok, _ = run(halloween.donner(eco, membre(uid=42), membre(), -100))
        self.assertTrue(ok)
        self.assertEqual(pb.d["hal_soldes"][0]["bonbons"], 0)  # jamais négatif

    def test_refus_non_gerant(self):
        pb = FauxPb(base(5))
        ok, _ = run(halloween.donner(FauxEco(pb), membre(uid=8), membre(), 10))
        self.assertFalse(ok)
        self.assertTrue(run(halloween.est_admin(FauxEco(pb), membre(uid=8, admin=True))))


class Vues(unittest.TestCase):
    def test_emoji_serveur(self):
        self.assertEqual(halloween.emoji_composant("<:citrouille:123>"), {"id": "123", "name": "citrouille", "animated": False})
        self.assertEqual(halloween.emoji_composant("🎃"), {"name": "🎃"})

    def test_vitrine_trois_boutons(self):
        pb = FauxPb(base(5))
        v = run(halloween.vitrine(FauxEco(pb)))
        boutons = v["components"][0]["components"][1]["components"]
        self.assertEqual([b["emoji"]["name"] for b in boutons], ["🎃", "👻", "🍬"])
        self.assertTrue(all(b["custom_id"].startswith("eco:hal:v:") for b in boutons))
        self.assertFalse(v["flags"] & halloween.eco_vues.PRIVE)

    def test_vue_boutique(self):
        pb = FauxPb(base(5, [ROLE, MYSTERE], Mystere.LOTS))
        v = run(halloween.vue(FauxEco(pb), membre()))
        textes = str(v)
        self.assertIn("Rôle Citrouille", textes)
        self.assertIn("eco:hal:buy:a_mys", textes)
        self.assertIn("Objet mystère : 2 surprises", textes)
        for c in v["components"][0]["components"]:
            for b in c.get("components", []) if c.get("type") == 1 else []:
                self.assertLessEqual(len(b["custom_id"]), 100)
        filtre = run(halloween.vue(FauxEco(pb), membre(), 0, "mystere"))
        self.assertNotIn("Rôle Citrouille", str(filtre))

    def test_vue_fermee_et_menu(self):
        pb = FauxPb(base(5))
        pb.d["hal_reglages"][0]["ouverte"] = False
        self.assertIn("fermée", str(run(halloween.vue(FauxEco(pb), membre()))))
        self.assertIsNone(run(halloween.option_menu(FauxEco(pb))))
        pb.d["hal_reglages"][0]["ouverte"] = True
        self.assertEqual(run(halloween.option_menu(FauxEco(pb)))["value"], "h_halloween")


if __name__ == "__main__":
    unittest.main()
