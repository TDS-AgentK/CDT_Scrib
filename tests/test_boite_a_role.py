"""Tests de /boite-a-role : la boîte se paie en monnaie de domaine (30), en Or (200) ou depuis l'inventaire,
puis le rôle se paie 10 dans la monnaie de son domaine (et non son prix en boutique) ; rien n'est prélevé en cas d'échec.
Lancer : python -m unittest discover -s tests
"""
import asyncio
import re
import unittest
from types import SimpleNamespace

from app import ros_boutique

ACAD = {"id": "dacad", "nom": "Académie", "ordre": 1, "monnaie_nom": "Fragments de savoir"}
MAIRIE = {"id": "dmairie", "nom": "Mairie", "ordre": 4, "monnaie_nom": "Influence"}
PRINCE = {"id": "rprince", "libelle": "Obtenir le rôle Prince", "domaine": "dmairie", "prix": 40, "actif": True}
ERUDIT = {"id": "rerudit", "libelle": "Obtenir le rôle Aspirant Erudit", "domaine": "dacad", "prix": 40, "actif": True}
BOITE = {"id": "rboite", "libelle": "Boîte à rôle x1", "domaine": "", "toute_monnaie": True, "prix": 30, "actif": True}
OBJET_BOITE = {"id": "oboite", "nom": "Boîte à rôle", "actif": True}


class FauxPb:
    """Base en mémoire : les filtres « champ="valeur" » et « champ=true » sont respectés, le reste ignoré."""

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

    async def lister(self, collection, filtre=None, tri=None):
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

    async def supprimer(self, collection, rid):
        self.d[collection] = [r for r in self.d[collection] if r["id"] != rid]

    async def requete(self, methode, chemin):
        return next(r for r in self.d["joueurs"] if r["id"] == chemin.rsplit("/", 1)[1])


class FauxEco:
    def __init__(self, pb):
        self.pb, self.roles_donnes = pb, []
        self._verrous = {}

    async def config(self):
        return {"reglages": {"rostheim_actif": True}, "salons": []}

    def _verrou(self, uid):
        return self._verrous.setdefault(uid, asyncio.Lock())

    @staticmethod
    def _a_le_role(membre, role_id, nom):
        return any(r.name == nom for r in membre.roles)

    async def joueur_de(self, membre):
        return self.pb.d["joueurs"][0]

    async def donner_role(self, membre, joueur, role_id, nom, jours):
        self.roles_donnes.append(nom)
        return ""

    async def ajouter_objet(self, joueur_id, objet_id, q, motif, note=""):
        ligne = next((l for l in self.pb.d["eco_inventaire"] if l["objet"] == objet_id), None)
        if ligne:
            ligne["quantite"] += q
        else:
            self.pb.d["eco_inventaire"].append({"id": "inv2", "joueur": joueur_id, "objet": objet_id, "quantite": q})

    async def tracer_mouvement(self, *args):
        pass


def base(or_=0, savoir=0, influence=0, boites=0):
    return FauxPb({
        "ros_domaines": [ACAD, MAIRIE], "ros_recompenses": [PRINCE, ERUDIT, BOITE],
        "ros_commandes": [{"id": "cbar", "type": "boite_a_role", "actif": True, "commentaires": "", "cout_monnaie": 10}],
        "joueurs": [{"id": "j1", "eco_or": or_}],
        "ros_soldes": [{"id": "s1", "joueur": "j1", "domaine": "dacad", "monnaie": savoir},
                       {"id": "s2", "joueur": "j1", "domaine": "dmairie", "monnaie": influence}],
        "eco_objets": [OBJET_BOITE],
        "eco_inventaire": [{"id": "inv1", "joueur": "j1", "objet": "oboite", "quantite": boites}] if boites else [],
    })


def membre(*roles):
    return SimpleNamespace(id=42, roles=[SimpleNamespace(name=r) for r in roles], mention="@joueur", display_name="Joueur")


def soldes(pb):
    return {s["domaine"]: s["monnaie"] for s in pb.d["ros_soldes"]}


def lancer(coro):
    return asyncio.run(coro)


class BoiteARole(unittest.TestCase):
    def test_contre_une_monnaie(self):
        pb = base(savoir=30, influence=10)
        eco = FauxEco(pb)
        ok, txt = lancer(ros_boutique.ouvrir_boite(eco, membre(), "rprince", "dacad"))
        self.assertTrue(ok, txt)
        self.assertEqual(eco.roles_donnes, ["Prince"])
        self.assertEqual(soldes(pb), {"dacad": 0, "dmairie": 0})

    def test_meme_monnaie_que_le_role(self):
        pb = base(savoir=40)
        ok, _ = lancer(ros_boutique.ouvrir_boite(FauxEco(pb), membre(), "rerudit", "dacad"))
        self.assertTrue(ok)
        self.assertEqual(soldes(pb)["dacad"], 0)

    def test_meme_monnaie_insuffisante_rien_preleve(self):
        pb = base(savoir=35)
        eco = FauxEco(pb)
        ok, txt = lancer(ros_boutique.ouvrir_boite(eco, membre(), "rerudit", "dacad"))
        self.assertFalse(ok)
        self.assertIn("Il faut 40", txt)
        self.assertEqual(soldes(pb)["dacad"], 35)
        self.assertEqual(eco.roles_donnes, [])

    def test_monnaie_insuffisante_pour_la_boite(self):
        pb = base(savoir=29, influence=10)
        ok, txt = lancer(ros_boutique.ouvrir_boite(FauxEco(pb), membre(), "rprince", "dacad"))
        self.assertFalse(ok)
        self.assertIn("Il faut 30", txt)
        self.assertEqual(soldes(pb), {"dacad": 29, "dmairie": 10})

    def test_contre_de_l_or(self):
        pb = base(or_=250, influence=10)
        ok, txt = lancer(ros_boutique.ouvrir_boite(FauxEco(pb), membre(), "rprince", "or"))
        self.assertTrue(ok, txt)
        self.assertEqual(pb.d["joueurs"][0]["eco_or"], 50)
        self.assertEqual(soldes(pb)["dmairie"], 0)

    def test_or_rendu_si_role_deja_possede(self):
        pb = base(or_=250, influence=10)
        ok, _ = lancer(ros_boutique.ouvrir_boite(FauxEco(pb), membre("Prince"), "rprince", "or"))
        self.assertFalse(ok)
        self.assertEqual(pb.d["joueurs"][0]["eco_or"], 250)
        self.assertEqual(soldes(pb)["dmairie"], 10)

    def test_depuis_l_inventaire(self):
        pb = base(influence=10, boites=1)
        ok, txt = lancer(ros_boutique.ouvrir_boite(FauxEco(pb), membre(), "rprince", "inv"))
        self.assertTrue(ok, txt)
        self.assertEqual(pb.d["eco_inventaire"], [])

    def test_inventaire_vide(self):
        ok, txt = lancer(ros_boutique.ouvrir_boite(FauxEco(base(influence=10)), membre(), "rprince", "inv"))
        self.assertFalse(ok)
        self.assertIn("pas de Boîte", txt)

    def test_role_deja_possede(self):
        pb = base(savoir=30, influence=10)
        ok, txt = lancer(ros_boutique.ouvrir_boite(FauxEco(pb), membre("Prince"), "rprince", "dacad"))
        self.assertFalse(ok)
        self.assertEqual(soldes(pb), {"dacad": 30, "dmairie": 10})

    def test_vue_avant_et_apres_le_choix_du_role(self):
        eco = FauxEco(base(savoir=12, boites=2))
        vue = lancer(ros_boutique.vue_boite(eco, membre("Prince")))
        blocs = vue["components"][0]["components"]
        menus = [b["components"][0] for b in blocs if b["type"] == 1]
        self.assertEqual([o["label"] for o in menus[0]["options"]], ["Aspirant Erudit"])  # Prince déjà possédé
        self.assertIn("+10 Fragments de savoir", menus[0]["options"][0]["description"])
        self.assertIn("vous en avez 12", menus[0]["options"][0]["description"])
        self.assertTrue(menus[1]["disabled"])
        self.assertEqual([o["value"] for o in menus[1]["options"]], ["dacad", "dmairie", "or", "inv"])
        self.assertEqual(menus[1]["options"][3]["description"], "vous en avez 2")
        self.assertIn("aspirant érudit", blocs[0]["content"])

        vue = lancer(ros_boutique.vue_boite(eco, membre("Prince"), "rerudit"))
        menus = [b["components"][0] for b in vue["components"][0]["components"] if b["type"] == 1]
        self.assertTrue(menus[0]["options"][0]["default"])
        self.assertFalse(menus[1]["disabled"])
        self.assertEqual(menus[1]["custom_id"], "eco:bxp:42:rerudit")

if __name__ == "__main__":
    unittest.main()
