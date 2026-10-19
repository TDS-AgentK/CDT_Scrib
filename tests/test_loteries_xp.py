"""XP gagnée aux loteries : créditée au tirage comme l'XP des messages (eco_xp, eco_gains « XP », montée de niveau).
Lancer : python -m unittest discover -s tests
"""
import asyncio
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from app import loteries

COURBE = {"formule": "personnalisee", "base_xp": 100, "pas_xp": 0, "courbure": 0, "arrondi": 1}


def tirer(loterie: dict, tickets: list[dict], xp_depart: int = 0, membre=None):
    envoyes, maj, gains = [], [], []
    joueurs = {"jA": {"id": "jA", "discord_id": "7", "eco_xp": xp_depart}, "jB": {"id": "jB", "discord_id": "8", "eco_xp": xp_depart}}

    async def requete(m, chemin):
        return dict(loterie) if "eco_loteries" in chemin else dict(joueurs[chemin.rsplit("/", 1)[1]])

    async def maj_pb(c, i, d):
        maj.append((c, i, d))
        if c == "joueurs":
            joueurs[i].update(d)
            return dict(joueurs[i])
        return {}

    async def gain(eco, jid, nom, monnaie, montant, origine):
        gains.append((jid, nom, monnaie, montant))

    async def send(embed=None, **k):
        envoyes.append(embed.description)
    guild = SimpleNamespace(get_member=lambda i: membre, fetch_member=mock.AsyncMock(return_value=membre))
    salon = SimpleNamespace(guild=guild, send=send)
    eco = SimpleNamespace(pb=SimpleNamespace(requete=requete, lister=mock.AsyncMock(return_value=tickets), maj=maj_pb),
                          client=SimpleNamespace(get_channel=lambda i: salon, get_guild=lambda i: None), guild_id=None,
                          config=mock.AsyncMock(return_value={"reglages": {"xp_nom": "XP Dranig"}, "courbe": COURBE}),
                          donner_succes=mock.AsyncMock(), ajouter_objet=mock.AsyncMock(), donner_role=mock.AsyncMock(), monter_niveau=mock.AsyncMock())
    with mock.patch.object(loteries, "changer_or", mock.AsyncMock()) as or_, mock.patch.object(loteries, "_gain", gain), \
            mock.patch.object(loteries.eco_vues, "or_txt", lambda c: "Or"):
        asyncio.run(loteries.tirer(eco, loterie))
    return SimpleNamespace(envoyes=envoyes, maj=maj, gains=gains, eco=eco, or_=or_, joueurs=joueurs)


BASE = {"id": "L1", "titre": "Loterie XP", "statut": "ouverte", "nombre_gagnants": 2, "salon_id": "1", "message_id": "",
        "or_lot": 0, "objet": "", "role_id": "", "succes": "", "xp_lot": 0}


class LoterieXp(unittest.TestCase):
    def test_texte_du_lot(self):
        cfg = {"reglages": {"xp_nom": "XP Dranig", "xp_emoji": "✨"}}
        with mock.patch.object(loteries.eco_vues, "or_txt", lambda c: "Or"):
            self.assertEqual(loteries._lot_txt({"xp_lot": 1500}, None, cfg), "**1 500** ✨ XP Dranig")
            self.assertEqual(loteries._lot_txt({"or_lot": 10, "xp_lot": 50}, None, cfg), "**10** Or + **50** ✨ XP Dranig")

    def test_tirage_credite_xp_et_trace(self):
        r = tirer({**BASE, "xp_lot": 40}, [{"joueur": "jA", "quantite": 1}, {"joueur": "jB", "quantite": 1}], xp_depart=10)
        self.assertEqual(r.joueurs["jA"]["eco_xp"], 50)
        self.assertEqual(r.joueurs["jB"]["eco_xp"], 50)
        self.assertEqual(sorted(g for g in r.gains), [("jA", "loterie : Loterie XP", "XP", 40), ("jB", "loterie : Loterie XP", "XP", 40)])
        self.assertEqual(r.or_.await_count, 0)
        r.eco.monter_niveau.assert_not_awaited()  # 10 → 50 : toujours niveau 0 (100 XP pour monter)
        self.assertIn("**40** XP Dranig", r.envoyes[0])

    def test_montee_de_niveau_annoncee(self):
        membre = SimpleNamespace(mention="<@7>")
        r = tirer({**BASE, "xp_lot": 250, "nombre_gagnants": 1}, [{"joueur": "jA", "quantite": 1}], xp_depart=90, membre=membre)
        self.assertEqual(r.joueurs["jA"]["eco_xp"], 340)
        r.eco.monter_niveau.assert_awaited_once()
        _, m, joueur, avant, apres = r.eco.monter_niveau.await_args.args
        self.assertIs(m, membre)
        self.assertEqual((joueur["eco_xp"], avant, apres), (340, 0, 3))

    def test_sans_xp_rien_de_credite(self):
        r = tirer({**BASE, "or_lot": 5}, [{"joueur": "jA", "quantite": 1}])
        self.assertEqual([g for g in r.gains if g[2] == "XP"], [])
        self.assertFalse(any(c == "joueurs" for c, _, _ in r.maj))

    def test_modeles(self):
        self.assertIn("xp_lot", loteries.CHAMPS_MODELE)
        self.assertEqual(loteries._copie({"xp_lot": 30, "titre": "T"})["xp_lot"], 30)

    def test_creer_avec_seulement_de_l_xp(self):
        cree = {}

        async def creer(col, d):
            cree[col] = d
            return {**d, "id": "L9"}
        eco = SimpleNamespace(pb=SimpleNamespace(creer=creer))
        fin = (datetime.now(loteries.PARIS) + timedelta(days=1)).strftime("%d/%m/%Y %H:%M")
        with mock.patch.object(loteries, "est_admin", mock.AsyncMock(return_value=True)), mock.patch.object(loteries, "publier", mock.AsyncMock()):
            ok, txt = asyncio.run(loteries.creer(eco, SimpleNamespace(id=1, guild=None), {"titre": "T", "fin": fin, "xp": 75}, "5"))
            self.assertTrue(ok, txt)
            self.assertEqual(cree["eco_loteries"]["xp_lot"], 75)
            ok, txt = asyncio.run(loteries.creer(eco, SimpleNamespace(id=1, guild=None), {"titre": "T", "fin": fin}, "5"))
            self.assertFalse(ok)
            self.assertIn("XP", txt)

    def test_option_de_la_commande(self):
        with mock.patch.dict("os.environ", {"DISCORD_TOKEN": "x", "DISCORD_APPLICATION_ID": "1"}):
            from scripts.register_commands import COMMANDS
        creer = next(o for o in next(c for c in COMMANDS if c["name"] == "loterie")["options"] if o["name"] == "creer")
        xp = next(o for o in creer["options"] if o["name"] == "xp")
        self.assertEqual((xp["type"], xp["min_value"], xp["required"]), (4, 1, False))
        self.assertLessEqual(len(creer["options"]), 25)

if __name__ == "__main__":
    unittest.main()
