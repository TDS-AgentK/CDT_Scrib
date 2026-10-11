"""Rôles gagnés aux loteries (définitifs ou temporaires) et retrait des rôles temporaires.
Lancer : python -m unittest discover -s tests
"""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import discord

from app import loteries
from app.economie import Economie, _date


class Role:
    def __init__(self, id_, nom, position, defaut=False, gere=False):
        self.id, self.name, self.position, self._defaut, self.managed = id_, nom, position, defaut, gere

    def is_default(self):
        return self._defaut

    def __lt__(self, autre):
        return self.position < autre.position

    def __ge__(self, autre):
        return self.position >= autre.position


def erreur_http(statut=403):
    return discord.Forbidden(SimpleNamespace(status=statut, reason="Forbidden"), "Missing Permissions")


BOT = Role(1, "Bot", 10)
VIP = Role(50, "VIP", 5)
HAUT = Role(60, "Grand Maître", 20)


def guilde(manage_roles=True, membres=None):
    roles = {r.id: r for r in (BOT, VIP, HAUT)}
    membres = membres or {}

    async def fetch_member(i):
        raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Member")
    g = SimpleNamespace(id=999, me=SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=manage_roles), top_role=BOT),
                        get_role=lambda i: roles.get(int(i)), get_member=lambda i: membres.get(int(i)), fetch_member=fetch_member)
    for m in membres.values():
        m.guild = g
    return g


def membre(id_, roles=(), echec_ajout=False):
    m = SimpleNamespace(id=id_, roles=list(roles))

    async def add_roles(role, reason=None):
        if echec_ajout:
            raise erreur_http()
        m.roles.append(role)

    async def remove_roles(role, reason=None):
        m.roles.remove(role)
    m.add_roles, m.remove_roles = add_roles, remove_roles
    return m


class FausseBase:
    def __init__(self):
        self.temp, self.maj_faites, self.n = {}, [], 0

    async def premier(self, col, filtre):
        return next((t for t in self.temp.values() if f'discord_user_id="{t["discord_user_id"]}"' in filtre and f'role_id="{t["role_id"]}"' in filtre), None)

    async def creer(self, col, d):
        self.n += 1
        self.temp[str(self.n)] = {**d, "id": str(self.n)}
        return self.temp[str(self.n)]

    async def maj(self, col, id_, d):
        self.maj_faites.append((col, id_, d))
        if col == "eco_roles_temporaires":
            self.temp[id_].update(d)


def economie(pb, g=None):
    e = SimpleNamespace(pb=pb, client=SimpleNamespace(get_guild=lambda i: g), _role_discord=lambda guild, rid, nom: guild.get_role(rid))
    e.donner_role = lambda *a, **k: Economie.donner_role(e, *a, **k)
    e.retirer_role_expire = lambda t: Economie.retirer_role_expire(e, t)
    return e


class DureeRole(unittest.TestCase):
    def test_lecture(self):
        self.assertEqual(loteries.lire_duree_role(""), 0)
        self.assertEqual(loteries.lire_duree_role(None), 0)
        self.assertEqual(loteries.lire_duree_role("7j"), 168)
        self.assertEqual(loteries.lire_duree_role("12h"), 12)
        self.assertEqual(loteries.lire_duree_role("1j12h"), 36)
        self.assertEqual(loteries.lire_duree_role("2d"), 48)
        self.assertEqual(loteries.lire_duree_role("3"), 72)
        self.assertIsNone(loteries.lire_duree_role("une semaine"))
        self.assertIsNone(loteries.lire_duree_role("h"))

    def test_texte_du_lot(self):
        cfg = {"reglages": {}}
        with mock.patch.object(loteries.eco_vues, "or_txt", lambda c: "Or"):
            self.assertIn("<@&50> (7 j)", loteries._lot_txt({"or_lot": 10, "role_id": "50", "role_heures": 168}, None, cfg))
            self.assertIn("<@&50> (définitif)", loteries._lot_txt({"role_id": "50", "role_heures": 0}, None, cfg))
            self.assertIn("1 j 12 h", loteries._lot_txt({"role_id": "50", "role_heures": 36}, None, cfg))

    def test_champs_des_modeles(self):
        for c in ("role_id", "role_nom", "role_heures"):
            self.assertIn(c, loteries.CHAMPS_MODELE)


class RoleDonnable(unittest.TestCase):
    def test_cas(self):
        self.assertIsNone(loteries.role_donnable(guilde(), VIP))
        self.assertIn("au-dessus", loteries.role_donnable(guilde(), HAUT))
        self.assertIn("Gérer les rôles", loteries.role_donnable(guilde(manage_roles=False), VIP))
        self.assertIn("introuvable", loteries.role_donnable(guilde(), None))
        self.assertIn("@everyone", loteries.role_donnable(guilde(), Role(999, "@everyone", 0, defaut=True)))


class DonnerRole(unittest.TestCase):
    def test_temporaire_cree_puis_prolonge(self):
        pb, m = FausseBase(), membre(7)
        e = economie(pb, guilde())
        m.guild = guilde()
        self.assertIsNone(asyncio.run(e.donner_role(m, {"id": "j7"}, "50", "VIP", 1, origine="loterie : Test")))
        self.assertIn(VIP, m.roles)
        self.assertEqual(len(pb.temp), 1)
        t = next(iter(pb.temp.values()))
        self.assertEqual(t["origine"], "loterie : Test")
        fin1 = _date(t["expire_le"])
        self.assertAlmostEqual((fin1 - datetime.now(timezone.utc)).total_seconds(), 86400, delta=60)
        # Gagné une 2e fois : même fiche, échéance prolongée d'un jour.
        self.assertIsNone(asyncio.run(e.donner_role(m, {"id": "j7"}, "50", "VIP", 1)))
        self.assertEqual(len(pb.temp), 1)
        self.assertAlmostEqual((_date(t["expire_le"]) - fin1).total_seconds(), 86400, delta=60)

    def test_deja_definitif_reste_definitif(self):
        pb, m = FausseBase(), membre(8, roles=[VIP])
        m.guild = guilde()
        self.assertIsNone(asyncio.run(economie(pb).donner_role(m, {"id": "j8"}, "50", "VIP", 2)))
        self.assertEqual(pb.temp, {})
        self.assertIn(VIP, m.roles)

    def test_definitif(self):
        pb, m = FausseBase(), membre(9)
        m.guild = guilde()
        self.assertIsNone(asyncio.run(economie(pb).donner_role(m, {"id": "j9"}, "50", "VIP", 0)))
        self.assertIn(VIP, m.roles)
        self.assertEqual(pb.temp, {})

    def test_permission_manquante(self):
        pb, m = FausseBase(), membre(10, echec_ajout=True)
        m.guild = guilde()
        raison = asyncio.run(economie(pb).donner_role(m, {"id": "j10"}, "50", "VIP", 1))
        self.assertIn("Gérer les rôles", raison)
        self.assertEqual(pb.temp, {})


class RetraitExpire(unittest.TestCase):
    def fiche(self, uid):
        return {"id": "t1", "guild_id": "999", "discord_user_id": str(uid), "role_id": "50", "erreur": ""}

    def test_retrait_normal(self):
        m = membre(7, roles=[VIP])
        e = economie(FausseBase(), guilde(membres={7: m}))
        self.assertTrue(asyncio.run(e.retirer_role_expire(self.fiche(7))))
        self.assertNotIn(VIP, m.roles)

    def test_membre_parti(self):
        e = economie(FausseBase(), guilde())
        self.assertTrue(asyncio.run(e.retirer_role_expire(self.fiche(42))))

    def test_permission_manquante_garde_la_fiche(self):
        m = membre(7, roles=[VIP])

        async def refus(role, reason=None):
            raise erreur_http()
        m.remove_roles = refus
        pb = FausseBase()
        pb.temp["t1"] = self.fiche(7)
        e = economie(pb, guilde(membres={7: m}))
        self.assertFalse(asyncio.run(e.retirer_role_expire(pb.temp["t1"])))
        self.assertIn("Gérer les rôles", pb.temp["t1"]["erreur"])


class Tirage(unittest.TestCase):
    def _tirer(self, role_id, heures, membres):
        g = guilde(membres=membres)
        pb = FausseBase()
        envoyes, maj = [], []
        joueurs = {"jA": {"id": "jA", "discord_id": "7", "pseudo": "A"}, "jB": {"id": "jB", "discord_id": "8", "pseudo": "B"}}
        loterie = {"id": "L1", "titre": "Test", "statut": "ouverte", "nombre_gagnants": 2, "or_lot": 5, "salon_id": "1",
                   "role_id": role_id, "role_nom": "", "role_heures": heures, "message_id": ""}

        async def requete(m, chemin):
            if "eco_loteries" in chemin:
                return dict(loterie)
            return joueurs[chemin.rsplit("/", 1)[1]]

        async def lister(col, f):
            return [{"joueur": "jA", "quantite": 1}, {"joueur": "jB", "quantite": 1}]

        async def maj_pb(col, id_, d):
            maj.append((col, d))
            if col == "eco_roles_temporaires":
                pb.temp[id_].update(d)

        async def send(embed=None, **k):
            envoyes.append(embed.description)
        salon = SimpleNamespace(guild=g, send=send)
        eco = economie(SimpleNamespace(requete=requete, lister=lister, maj=maj_pb, premier=pb.premier, creer=pb.creer), g)
        eco.guild_id, eco.config = 999, mock.AsyncMock(return_value={"reglages": {}})
        eco.client.get_channel = lambda i: salon
        eco.donner_succes, eco.ajouter_objet = mock.AsyncMock(), mock.AsyncMock()
        with mock.patch.object(loteries, "changer_or", mock.AsyncMock()) as or_, mock.patch.object(loteries, "_gain", mock.AsyncMock()), \
                mock.patch.object(loteries.eco_vues, "or_txt", lambda c: "Or"):
            asyncio.run(loteries.tirer(eco, loterie))
        return pb, maj, envoyes, or_

    def test_role_temporaire_donne_aux_gagnants(self):
        mA, mB = membre(7), membre(8)
        pb, maj, envoyes, or_ = self._tirer("50", 168, {7: mA, 8: mB})
        self.assertIn(VIP, mA.roles)
        self.assertIn(VIP, mB.roles)
        self.assertEqual(len(pb.temp), 2)
        self.assertTrue(all(abs((_date(t["expire_le"]) - datetime.now(timezone.utc) - timedelta(days=7)).total_seconds()) < 60 for t in pb.temp.values()))
        self.assertEqual(or_.await_count, 2)
        self.assertNotIn("⚠️", envoyes[0])
        self.assertEqual(next(d for c, d in maj if "gagnants_ids" in d)["erreurs_lot"], "")

    def test_role_trop_haut_ne_bloque_pas(self):
        mA, mB = membre(7), membre(8)
        pb, maj, envoyes, or_ = self._tirer("60", 0, {7: mA, 8: mB})
        self.assertEqual(or_.await_count, 2)  # l'Or est quand même donné
        self.assertNotIn(HAUT, mA.roles)
        self.assertIn("⚠️ Rôle non donné", envoyes[0])
        self.assertIn("au-dessus", envoyes[0])
        erreurs = next(d for c, d in maj if "gagnants_ids" in d)["erreurs_lot"]
        self.assertEqual(len(erreurs.splitlines()), 2)

    def test_gagnant_parti_du_serveur(self):
        mA = membre(7)
        _, maj, envoyes, _ = self._tirer("50", 24, {7: mA})
        self.assertIn(VIP, mA.roles)
        self.assertIn("n'est plus sur le serveur", envoyes[0])


class Commande(unittest.TestCase):
    def test_options(self):
        with mock.patch.dict("os.environ", {"DISCORD_TOKEN": "x", "DISCORD_APPLICATION_ID": "1"}):
            from scripts.register_commands import COMMANDS
        creer = next(o for o in next(c for c in COMMANDS if c["name"] == "loterie")["options"] if o["name"] == "creer")
        noms = {o["name"]: o for o in creer["options"]}
        self.assertEqual(noms["role"]["type"], 8)
        self.assertFalse(noms["role"]["required"])
        self.assertIn("role_duree", noms)
        self.assertLessEqual(len(creer["options"]), 25)


if __name__ == "__main__":
    unittest.main()
