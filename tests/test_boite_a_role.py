"""Tests de /boite-a-role : la boîte se paie en monnaie de domaine (30), en Or (200) ou depuis l'inventaire,
puis le rôle se paie dans la monnaie de son domaine (10) ; si le rôle n'est pas obtenu, le paiement est rendu.
Lancer : python -m unittest discover -s tests
"""
import asyncio
import re
import unittest
from types import SimpleNamespace

from app import ros_boutique

ACAD = {"id": "dacad", "nom": "Académie", "ordre": 1, "monnaie_nom": "Fragments de savoir"}
MAIRIE = {"id": "dmairie", "nom": "Mairie", "ordre": 4, "monnaie_nom": "Influence"}
PRINCE = {"id": "rprince", "libelle": "Obtenir le rôle Prince", "domaine": "dmairie", "prix": 10, "actif": True}
ERUDIT = {"id": "rerudit", "libelle": "Obtenir le rôle Aspirant Erudit", "domaine": "dacad", "prix": 10, "actif": True}
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
        "ros_commandes": [{"id": "cbar", "type": "boite_a_role", "actif": True, "commentaires": ""}],
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
        ok, txt = lancer(ros_boutique.ouvrir_boite(eco, membre(), "rprince", None, "dacad"))
        self.assertTrue(ok, txt)
        self.assertEqual(eco.roles_donnes, ["Prince"])
        self.assertEqual(soldes(pb), {"dacad": 0, "dmairie": 0})

    def test_meme_monnaie_que_le_role(self):
        pb = base(savoir=40)
        ok, _ = lancer(ros_boutique.ouvrir_boite(FauxEco(pb), membre(), "rerudit", None, "dacad"))
        self.assertTrue(ok)
        self.assertEqual(soldes(pb)["dacad"], 0)

    def test_monnaie_du_role_insuffisante_rend_la_boite(self):
        pb = base(savoir=35)
        eco = FauxEco(pb)
        ok, txt = lancer(ros_boutique.ouvrir_boite(eco, membre(), "rerudit", None, "dacad"))
        self.assertFalse(ok)
        self.assertIn("rendus", txt)
        self.assertEqual(soldes(pb)["dacad"], 35)
        self.assertEqual(eco.roles_donnes, [])

    def test_monnaie_insuffisante_pour_la_boite(self):
        pb = base(savoir=29, influence=10)
        ok, txt = lancer(ros_boutique.ouvrir_boite(FauxEco(pb), membre(), "rprince", None, "dacad"))
        self.assertFalse(ok)
        self.assertIn("Il faut 30", txt)
        self.assertEqual(soldes(pb), {"dacad": 29, "dmairie": 10})

    def test_contre_de_l_or(self):
        pb = base(or_=250, influence=10)
        ok, txt = lancer(ros_boutique.ouvrir_boite(FauxEco(pb), membre(), "rprince", None, "or"))
        self.assertTrue(ok, txt)
        self.assertEqual(pb.d["joueurs"][0]["eco_or"], 50)
        self.assertEqual(soldes(pb)["dmairie"], 0)

    def test_or_rendu_si_role_deja_possede(self):
        pb = base(or_=250, influence=10)
        ok, _ = lancer(ros_boutique.ouvrir_boite(FauxEco(pb), membre("Prince"), "rprince", None, "or"))
        self.assertFalse(ok)
        self.assertEqual(pb.d["joueurs"][0]["eco_or"], 250)
        self.assertEqual(soldes(pb)["dmairie"], 10)

    def test_depuis_l_inventaire(self):
        pb = base(influence=10, boites=1)
        ok, txt = lancer(ros_boutique.ouvrir_boite(FauxEco(pb), membre(), "rprince", None, "inv"))
        self.assertTrue(ok, txt)
        self.assertEqual(pb.d["eco_inventaire"], [])

    def test_inventaire_vide(self):
        ok, txt = lancer(ros_boutique.ouvrir_boite(FauxEco(base(influence=10)), membre(), "rprince", None, "inv"))
        self.assertFalse(ok)
        self.assertIn("pas de Boîte", txt)

    def test_menus(self):
        menus = lancer(ros_boutique.menus_boite(FauxEco(base()), membre("Prince")))
        self.assertEqual(len(menus), 2)
        (premier, selects), (second, autres) = menus
        self.assertIn("contre des fragments de savoir. (30)", premier["description"])
        self.assertIn("contre des influences. (30)", premier["description"])
        self.assertIn("aspirant érudit", premier["fields"][0]["value"])
        self.assertEqual([s["components"][0]["custom_id"] for s in selects + autres],
                         ["eco:bar:42:dacad", "eco:bar:42:dmairie", "eco:bar:42:or", "eco:bar:42:inv"])
        # Rôle déjà possédé : seul Aspirant Erudit reste proposé.
        self.assertEqual([o["label"] for o in autres[0]["components"][0]["options"]], ["Aspirant Erudit"])


if __name__ == "__main__":
    unittest.main()
