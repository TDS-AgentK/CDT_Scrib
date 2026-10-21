"""Tirages d'emblèmes : 5 emblèmes distincts par tirage, cumul N, Leveinard, pool insuffisant ou vide, collections,
pondération par la chronologie, doublons et journal.
Lancer : python -m unittest discover -s tests
"""
import asyncio
import random
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest import mock

from app import emblemes

MOI = "membre-moi"


def emb(i, cat="Personnage", serie="1256", possede=False, **k):
    return {"id": f"e{i}", "emblem_name": f"Emblème {i}", "collection": cat, "serie": serie, "numero": i,
            "joueur_en_possession": [MOI] if possede else [], "illustration": f"wix:image://v1/img{i}.jpg/x.jpg", **k}


class Base:
    def __init__(self, liste, stock=1, reglages=None, chronologie=None, personnages=None, type_tirage="embleme"):
        self.emblemes = {e["id"]: e for e in liste}
        self.stock = stock
        self.objet = {"id": "obj", "nom": "Tirage d'Emblème", "emb_tirage": type_tirage}
        self.chronologie = chronologie or []
        self.personnages = personnages or []
        self.journal, self.retraits, self.or_ = [], [], []
        self.reglages = {"cle": "general", "emb_poids_chronologie": 3, "emb_nb_entrees_chronologie": 3, "emb_or_doublon": 0, **(reglages or {})}

    async def lister(self, col, filtre=None, tri=None, expand=None):
        if col == "emblemes":
            return [dict(e, joueur_en_possession=list(e["joueur_en_possession"])) for e in self.emblemes.values()]
        if col == "personnages":
            return self.personnages
        return []

    async def requete(self, methode, chemin, json=None, params=None):
        if "eco_objets" in chemin:
            return dict(self.objet)
        if "chronologie" in chemin:
            return {"items": self.chronologie[: params["perPage"]]}
        raise AssertionError(chemin)

    async def maj(self, col, id_, d):
        assert col == "emblemes" and list(d) == ["joueur_en_possession+"]
        self.emblemes[id_]["joueur_en_possession"].append(d["joueur_en_possession+"])

    async def creer(self, col, d):
        assert col == "emb_tirages"
        self.journal.append(d)


def ouvrir(base: Base, demandes=1, serie="1256", graine=1):
    async def qte(eco, joueur_id, objet_id):
        return {}, base.stock

    async def retirer(eco, joueur_id, objet_id, q, motif, note=""):
        base.retraits.append(q)
        base.stock -= q

    async def changer(eco, joueur_id, delta):
        base.or_.append(delta)

    verrou = asyncio.Lock()
    eco = SimpleNamespace(pb=base, joueur_de=mock.AsyncMock(return_value={"id": "j1", "joueur": MOI}),
                          config=mock.AsyncMock(return_value={"reglages": base.reglages}), _verrou=lambda uid: verrou)
    membre = SimpleNamespace(id=42, mention="<@42>")
    with mock.patch.object(emblemes, "quantite", qte), mock.patch.object(emblemes, "retirer_objet", retirer), \
            mock.patch.object(emblemes, "changer_or", changer), mock.patch.object(emblemes, "_gain", mock.AsyncMock()), \
            mock.patch.object(emblemes.eco_vues, "or_txt", lambda c: "Or"):
        return asyncio.run(emblemes.ouvrir(eco, membre, "obj", demandes, serie, random.Random(graine)))


class Plan(unittest.TestCase):
    def test_plan(self):
        self.assertEqual(emblemes.plan(1, 100), (1, 5))
        self.assertEqual(emblemes.plan(3, 100), (3, 15))
        self.assertEqual(emblemes.plan(3, 7), (2, 7))   # 7 emblèmes : 2 tirages consommés, 1 rendu
        self.assertEqual(emblemes.plan(3, 10), (2, 10))
        self.assertEqual(emblemes.plan(2, 0), (0, 0))

    def test_pools(self):
        liste = [emb(1, "Lignée"), emb(2, "Famille"), emb(3, "Maison"), emb(4, "Faction"), emb(5), emb(6, possede=True), emb(7, serie="1257")]
        noms = lambda t, s="1256": sorted(e["id"] for e in emblemes.pool(liste, t, s, MOI))
        self.assertEqual(noms("lignee"), ["e1", "e2", "e3"])
        self.assertEqual(noms("faction"), ["e4"])
        self.assertEqual(noms("personnages"), ["e5", "e6"])
        self.assertEqual(noms("embleme"), ["e1", "e2", "e3", "e4", "e5", "e6"])
        self.assertEqual(noms("leveinard"), ["e1", "e2", "e3", "e4", "e5"])
        self.assertEqual(noms("embleme", "1257"), ["e7"])
        self.assertEqual(emblemes.serie_de({"serie": "nouvelle"}), "1257")
        self.assertEqual(emblemes.serie_de({}), "1256")


class Tirages(unittest.TestCase):
    def test_un_tirage_cinq_distincts(self):
        b = Base([emb(i) for i in range(40)])
        r = ouvrir(b)
        ids = [l["e"]["id"] for l in r["lignes"]]
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(set(ids)), 5)
        self.assertEqual(b.retraits, [1])
        self.assertEqual(sum(MOI in e["joueur_en_possession"] for e in b.emblemes.values()), 5)

    def test_cumul_n_tous_distincts(self):
        b = Base([emb(i) for i in range(60)], stock=10)
        r = ouvrir(b, demandes=7)
        ids = [l["e"]["id"] for l in r["lignes"]]
        self.assertEqual((len(ids), len(set(ids))), (35, 35))
        self.assertEqual(b.retraits, [7])
        self.assertEqual({j["tirages_consommes"] for j in b.journal}, {7})
        self.assertEqual(len({j["lot"] for j in b.journal}), 1)

    def test_quantite_plafonnee_au_stock(self):
        b = Base([emb(i) for i in range(60)], stock=2)
        r = ouvrir(b, demandes=9)
        self.assertEqual((r["demandes"], r["consommes"], len(r["lignes"])), (2, 2, 10))

    def test_doublon_hors_leveinard(self):
        b = Base([emb(i, possede=True) for i in range(5)], reglages={"emb_or_doublon": 4})
        r = ouvrir(b)
        self.assertTrue(all(l["doublon"] for l in r["lignes"]))
        self.assertEqual(b.or_, [4] * 5)
        self.assertTrue(all(j["doublon"] and j["or_doublon"] == 4 for j in b.journal))
        self.assertIn("déjà possédé", emblemes.resultat_embeds(r)[0]["description"])

    def test_leveinard_sans_possedes(self):
        b = Base([emb(i, possede=i < 30) for i in range(40)], stock=1, type_tirage="leveinard")
        for _ in range(20):
            r = ouvrir(b, graine=_)
            self.assertTrue(all(not l["doublon"] and int(l["e"]["id"][1:]) >= 30 for l in r["lignes"]))
            b.stock = 1
            for e in b.emblemes.values():
                e["joueur_en_possession"] = [MOI] if int(e["id"][1:]) < 30 else []

    def test_pool_insuffisant_consommation_partielle(self):
        b = Base([emb(i, possede=i >= 7) for i in range(20)], stock=5, type_tirage="leveinard")
        r = ouvrir(b, demandes=3)
        self.assertEqual(len(r["lignes"]), 7)
        self.assertEqual(b.retraits, [2])
        self.assertEqual(b.stock, 3)
        self.assertEqual(r["rendus"], 1)
        self.assertIn("non utilisé", emblemes.resultat_embeds(r)[0]["description"])

    def test_pool_vide_rien_consomme(self):
        b = Base([emb(i, possede=True) for i in range(10)], stock=2, type_tirage="leveinard")
        r = ouvrir(b, demandes=2)
        self.assertIn("Aucun tirage n'a été utilisé", r["erreur"])
        self.assertEqual((b.retraits, b.journal), ([], []))
        vide = ouvrir(Base([emb(1)], type_tirage="faction"))
        self.assertIn("erreur", vide)

    def test_collections(self):
        b = Base([emb(i, serie="1256") for i in range(10)] + [emb(100 + i, serie="1257") for i in range(10)])
        r = ouvrir(b, serie="1257")
        self.assertTrue(all(l["e"]["serie"] == "1257" for l in r["lignes"]))
        self.assertEqual({j["serie"] for j in b.journal}, {"1257"})
        self.assertEqual((r["obtenus"], r["total"]), (5, 10))
        self.assertIn("Collection 1257", emblemes.resultat_embeds(r)[0]["footer"]["text"])

    def test_ponderation_chronologie(self):
        liste = [emb(i, personnage="p-chrono" if i == 0 else f"p{i}") for i in range(30)]
        tirages = Counter()
        candidats = [e for e in liste]
        rng = random.Random(7)
        for _ in range(4000):
            tirages.update(e["id"] for e in emblemes.tirer(candidats, 1, {"p-chrono"}, 3, rng))
        autres = sum(tirages[f"e{i}"] for i in range(1, 30)) / 29
        self.assertGreater(tirages["e0"] / autres, 2.3)  # ≈ 3 attendu
        # Poids 1 : pas d'effet.
        self.assertEqual(emblemes.poids_de(liste[0], {"p-chrono"}, 1), 1.0)

    def test_ponderation_lue_dans_la_chronologie(self):
        liste = [emb(i, cat="Famille", lignee="L-chrono" if i == 0 else f"L{i}") for i in range(10)]
        b = Base(liste, chronologie=[{"portraits": ["perso1"], "factions": []}], personnages=[{"id": "perso1", "familles": ["L-chrono"]}])
        r = ouvrir(b)
        e0 = [j for j in b.journal if j["embleme"] == "e0"]
        if e0:
            self.assertTrue(e0[0]["pondere"])
        self.assertTrue(all(not j["pondere"] for j in b.journal if j["embleme"] != "e0"))
        self.assertEqual(len(r["lignes"]), 5)

    def test_journal_une_ligne_par_embleme(self):
        b = Base([emb(i, cat="Faction") for i in range(20)], stock=3, type_tirage="faction")
        ouvrir(b, demandes=3)
        self.assertEqual(len(b.journal), 15)
        self.assertEqual({j["type_tirage"] for j in b.journal}, {"faction"})
        self.assertEqual(len({j["embleme"] for j in b.journal}), 15)

    def test_embeds_nouveaux_en_premier_et_galerie(self):
        b = Base([emb(i, possede=i < 2) for i in range(6)], stock=2)
        r = ouvrir(b, demandes=2)
        embeds = emblemes.resultat_embeds(r)
        lignes = embeds[0]["description"].split("\n")
        premiers_doublons = [i for i, l in enumerate(lignes) if "déjà possédé" in l]
        nouveaux = [i for i, l in enumerate(lignes) if l.startswith("👤")]
        self.assertTrue(max(nouveaux) < min(premiers_doublons))
        self.assertEqual(len(embeds), 1 + 4)  # 4 nouveaux avec image
        self.assertEqual(len({e["url"] for e in embeds[1:]}), 1)
        self.assertLessEqual(len(embeds), 10)

    def test_galerie_limitee(self):
        b = Base([emb(i) for i in range(80)], stock=10)
        embeds = emblemes.resultat_embeds(ouvrir(b, demandes=10))
        self.assertEqual(len(embeds), 10)
        self.assertLess(sum(len(e.get("description", "")) for e in embeds), 6000)

    def test_image_url(self):
        self.assertEqual(emblemes.image_url("wix:image://v1/690a5f_ab~mv2.jpg/Nom.jpg#originWidth=1"), "https://static.wixstatic.com/media/690a5f_ab~mv2.jpg")
        self.assertTrue(emblemes.image_url("/media/uploads/x.png").endswith("/media/uploads/x.png"))
        self.assertEqual(emblemes.image_url(""), "")


if __name__ == "__main__":
    unittest.main()
