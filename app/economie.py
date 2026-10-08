"""Économie des Chroniques du Temps (connexion Gateway permanente) : XP et Or par message, niveaux et
récompenses, boutiques, inventaire. Tous les réglages viennent de la page « Économie du bot » du site
(collections eco_* de PocketBase) et sont relus toutes les minutes : rien n'est codé en dur ici.

Commandes (préfixe ECO_PREFIX, « ?? » par défaut, comme sur Draftbot) :
  ??niveau [@membre] · ??classement · ??inventaire · ??boutique · ??acheter <numéro ou nom>
  + commandes Rostheim et RP lues dans la base (voir app/rostheim.py).
"""
import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone

import discord

from app.pocketbase import PocketBase, echapper

log = logging.getLogger("cdt_scrib.economie")

RECHARGE_CONFIG_S = 60
COULEUR_DEFAUT = 0xC5A24F


# ---------------------------------------------------------------- courbe de niveau (identique au site)

def xp_pour_monter(courbe: dict, n: int) -> int:
    """XP pour passer du niveau n au niveau n+1 (niveau 0 au départ, comme Draftbot)."""
    if courbe.get("formule") == "personnalisee":
        brut = (courbe.get("base_xp") or 0) + (courbe.get("pas_xp") or 0) * n + (courbe.get("courbure") or 0) * n * n
    else:
        m = courbe.get("multiplicateur")
        brut = (1 if m is None else m) * (5 * n * n + 50 * n + 100)
    a = courbe.get("arrondi") or 0
    a = a if a > 1 else 1
    return max(0, round(brut / a) * a)


def niveau_de(courbe: dict, xp: int) -> tuple[int, int, int]:
    """(niveau, XP déjà faite dans ce niveau, XP pour monter au suivant)."""
    n, total = 0, 0
    maxi = courbe.get("niveau_max") or 0
    while not maxi or n < maxi:
        s = xp_pour_monter(courbe, n)
        if s <= 0 or total + s > xp:
            return n, xp - total, s
        total += s
        n += 1
    return n, xp - total, 0


def normaliser(nom: str | None) -> str:
    """Nom comparable entre la base et Discord : sans accents, emojis, séparateurs ni majuscules
    (« 📝・Textes-Libres » == « textes-libres », « Erudit » == « Érudit »)."""
    import unicodedata
    base = unicodedata.normalize("NFD", (nom or "").lstrip("@"))
    return "".join(c for c in base.lower() if c.isascii() and c.isalnum())


def meme_nom(a: str | None, b: str | None) -> bool:
    return bool(normaliser(a)) and normaliser(a) == normaliser(b)


def _date(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None


def _pb_date(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")


class Economie:
    def __init__(self, pb: PocketBase, client: discord.Client, prefixe: str = "??", guild_id: str | None = None):
        self.pb = pb
        self.client = client
        self.prefixe = prefixe
        self.guild_id = int(guild_id) if guild_id else None
        self.cfg: dict = {}
        self._charge_a = 0.0
        self._dernier_gain: dict[int, float] = {}
        self._verrous: dict[int, asyncio.Lock] = {}
        # ECO_DEBUG=1 : explique dans les journaux pourquoi chaque message rapporte (ou non) de l'XP.
        import os
        self.debug = os.getenv("ECO_DEBUG", "") not in ("", "0")
        from app.rostheim import Rostheim
        self.rostheim = Rostheim(self)

    # ------------------------------------------------------------ configuration (page Économie du site)

    async def config(self) -> dict:
        if self.cfg and time.time() - self._charge_a < RECHARGE_CONFIG_S:
            return self.cfg
        pb = self.pb
        reglages, salons, roles, multis, niveaux, recompenses, paliers, boutiques, articles = await asyncio.gather(
            pb.lister("eco_reglages"), pb.lister("eco_salons"), pb.lister("eco_roles_bonus"), pb.lister("eco_multiplicateurs"),
            pb.lister("eco_niveaux"), pb.lister("eco_niveaux_recompenses"), pb.lister("eco_paliers"),
            pb.lister("eco_boutiques"), pb.lister("eco_articles"),
        )
        self.cfg = {
            "reglages": next((r for r in reglages if r.get("cle") == "general"), {}),
            "salons": [s for s in salons if s.get("actif")],
            "roles": [r for r in roles if r.get("actif")],
            "multis": [m for m in multis if m.get("actif")],
            "courbe": next((n for n in niveaux if n.get("cle") == "courbe"), {}),
            "recompenses": [r for r in recompenses if r.get("actif")],
            "paliers": [p for p in paliers if p.get("actif")],
            "boutiques": sorted([b for b in boutiques if b.get("active")], key=lambda b: b.get("ordre") or 0),
            "articles": [a for a in articles if a.get("actif")],
        }
        self._charge_a = time.time()
        return self.cfg

    @staticmethod
    def rostheim_types(commande: str) -> tuple:
        from app.rostheim import TYPES_CONVERSION, TYPES_GAIN
        return TYPES_GAIN if commande == "recompense" else TYPES_CONVERSION

    def _verrou(self, uid: int) -> asyncio.Lock:
        return self._verrous.setdefault(uid, asyncio.Lock())

    @staticmethod
    def _a_le_role(member: discord.Member, role_id: str | None, role_nom: str | None) -> bool:
        for r in member.roles:
            if role_id and str(r.id) == str(role_id):
                return True
            if not role_id and role_nom and meme_nom(r.name, role_nom):
                return True
        return False

    def _role_discord(self, guild: discord.Guild, role_id: str | None, role_nom: str | None) -> discord.Role | None:
        if role_id:
            r = guild.get_role(int(role_id)) if str(role_id).isdigit() else None
            if r:
                return r
        if role_nom:
            return next((r for r in guild.roles if meme_nom(r.name, role_nom)), None)
        return None

    async def joueur_de(self, user: discord.abc.User) -> dict | None:
        """Fiche « joueurs » du membre : par ID Discord, sinon par pseudo Discord (l'ID est alors enregistré)."""
        j = await self.pb.premier("joueurs", f'discord_id="{echapper(user.id)}"')
        if j:
            return j
        j = await self.pb.premier("joueurs", f'pseudo_discord="{echapper(user.name)}"')
        if j and not j.get("discord_id"):
            j = await self.pb.maj("joueurs", j["id"], {"discord_id": str(user.id)})
        return j

    # ------------------------------------------------------------ messages

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
            return
        if self.guild_id and message.guild.id != self.guild_id:
            if self.debug:
                log.info("[XP] message ignoré : serveur %s différent d'ECO_GUILD_ID", message.guild.id)
            return
        try:
            if message.content.startswith(self.prefixe) or message.content.startswith("--"):
                await self.commande(message)
            else:
                await self.gain(message)
        except Exception:  # un incident ne doit jamais couper le bot
            log.exception("Erreur économie sur le message %s", message.id)

    def _salon(self, cfg: dict, channel) -> dict | None:
        """Salon éligible correspondant au message (fils et posts de forum selon les réglages)."""
        reglages = cfg["reglages"]
        if isinstance(channel, discord.Thread):
            parent = channel.parent
            if isinstance(parent, discord.ForumChannel):
                if not reglages.get("xp_forums"):
                    return None
            elif not reglages.get("xp_fils"):
                return None
            channel = parent
        if channel is None:
            return None
        for s in cfg["salons"]:
            if s.get("salon_id") and str(s["salon_id"]) == str(channel.id):
                return s
        # Sinon par le nom, même si un ID est renseigné (ex. ID du vrai serveur, test sur un serveur de test).
        return next((s for s in cfg["salons"] if meme_nom(s.get("salon_nom"), channel.name)), None)

    async def gain(self, message: discord.Message):
        cfg = await self.config()
        reglages, membre = cfg["reglages"], message.author
        def trace(raison: str):
            if self.debug:
                log.info("[XP] %s dans #%s (%s, type %s) : %s", membre.name, getattr(message.channel, "name", "?"),
                         message.channel.id, type(message.channel).__name__, raison)

        salon = self._salon(cfg, message.channel)
        if not salon or not salon.get("gain_xp"):
            trace("salon non éligible" if not salon else f'salon « {salon.get("salon_nom")} » sans gain d\'XP')
            return
        if any(r.get("sans_gain") and self._a_le_role(membre, r.get("role_id"), r.get("role_nom")) for r in cfg["roles"]):
            trace("rôle exclu")
            return
        attente = max(reglages.get("xp_intervalle_s") or 0, salon.get("cooldown_s") or 0)
        maintenant = time.time()
        if attente and maintenant - self._dernier_gain.get(membre.id, 0) < attente:
            trace(f"attente de {attente} s pas écoulée")
            return

        # Bonus de rôle : on garde le plus fort (pas de cumul entre rôles boosters).
        pct_xp = pct_or = 0
        for r in cfg["roles"]:
            if r.get("sans_gain") or not self._a_le_role(membre, r.get("role_id"), r.get("role_nom")):
                continue
            cible = salon["id"] in (r.get("salons_cibles") or [])
            pct_xp = max(pct_xp, (r.get("bonus_xp_pct") or 0) + ((r.get("bonus_cible_xp_pct") or 0) if cible else 0))
            pct_or = max(pct_or, (r.get("bonus_or_pct") or 0) + ((r.get("bonus_cible_or_pct") or 0) if cible else 0))
        f_xp = f_or = 1.0
        now = datetime.now(timezone.utc)
        for m in cfg["multis"]:
            debut, fin = _date(m.get("debut")), _date(m.get("fin"))
            if (debut and now < debut) or (fin and now > fin):
                continue
            if m.get("portee") == "salons" and salon["id"] not in (m.get("salons") or []):
                continue
            if m.get("min_caracteres") and len(message.content) <= m["min_caracteres"]:
                continue
            f_xp *= m.get("facteur_xp") or 1
            f_or *= m.get("facteur_or") or 1

        # Gain de base tiré au hasard entre les bornes des réglages (incluses), sinon valeur fixe du salon.
        xp_min = reglages.get("xp_min") or 0
        xp_max = max(xp_min, reglages.get("xp_max") or 0)
        xp = random.randint(xp_min, xp_max) if xp_max else (salon.get("xp_base") or 0)
        or_min = reglages.get("or_min") or 0
        or_max = max(or_min, reglages.get("or_max") or 0)
        or_ = random.randint(or_min, or_max) if or_max else (salon.get("or_base") or 0)
        xp *= (salon.get("multiplicateur_xp") or 1) * (1 + pct_xp / 100) * f_xp
        if reglages.get("messages_longs_double") and len(message.content) > 250:
            xp *= 2
        or_ *= (1 + pct_or / 100) * f_or
        xp, or_ = round(xp), round(or_)
        if xp <= 0 and or_ <= 0:
            trace("gain calculé nul")
            return

        async with self._verrou(membre.id):
            joueur = await self.joueur_de(membre)
            if not joueur:
                trace("aucune fiche joueur liée (ID Discord ni pseudo reconnus)")
                return  # membre sans fiche joueur sur le site
            trace(f'+{xp} XP, +{or_} Or (salon « {salon.get("salon_nom")} », bonus rôle {pct_xp} %)')
            self._dernier_gain[membre.id] = maintenant
            ancien = joueur.get("eco_xp") or 0
            nouvel_or = (joueur.get("eco_or") or 0) + or_
            maj = {"eco_xp": ancien + xp, "eco_or": nouvel_or}
            if nouvel_or > (joueur.get("eco_or_record") or 0):
                maj["eco_or_record"] = nouvel_or
            joueur = await self.pb.maj("joueurs", joueur["id"], maj)
            await self.tracer_message(joueur["id"], message, xp, or_)
            n_avant, n_apres = niveau_de(cfg["courbe"], ancien)[0], niveau_de(cfg["courbe"], joueur["eco_xp"])[0]
            if n_apres > n_avant:
                await self.monter_niveau(message.channel, membre, joueur, n_avant, n_apres)

    async def tracer_message(self, joueur_id: str, message: discord.Message, xp: int, or_: int):
        """Trace dans eco_gains l'XP et l'Or gagnés par un message (visible dans l'onglet Gains du site)."""
        salon = message.channel.parent if isinstance(message.channel, discord.Thread) else message.channel
        nom = f'message #{getattr(salon, "name", "?")}'
        date = _pb_date(datetime.now(timezone.utc))
        try:
            for monnaie, montant in (("XP", xp), ("Or", or_)):
                if montant:
                    await self.pb.creer("eco_gains", {"joueur": joueur_id, "commande": nom, "monnaie": monnaie, "montant": montant,
                                                      "points_jauge": 0, "date": date, "origine": message.jump_url})
        except Exception:  # la trace ne doit pas bloquer le gain
            log.exception("Trace du gain du message %s impossible", message.id)

    # ------------------------------------------------------------ niveaux et récompenses

    async def monter_niveau(self, channel, membre: discord.Member, joueur: dict, avant: int, apres: int):
        cfg = await self.config()
        lignes = []
        for niveau in range(avant + 1, apres + 1):
            for r in [r for r in cfg["recompenses"] if r.get("niveau") == niveau]:
                lignes.append(await self.donner_recompense(membre, joueur, r))
            for p in [p for p in cfg["paliers"] if p.get("declencheur") == "niveau" and p.get("seuil") == niveau]:
                await self.donner_succes(joueur, p.get("succes"))
        xp_nom = f'{cfg["reglages"].get("xp_emoji") or ""} {cfg["reglages"].get("xp_nom") or "XP"}'.strip()
        embed = discord.Embed(description=f"{membre.mention} atteint le niveau **{apres}** ({xp_nom}).", color=COULEUR_DEFAUT)
        if [l for l in lignes if l]:
            embed.add_field(name="Récompenses", value="\n".join(l for l in lignes if l)[:1024], inline=False)
        await channel.send(embed=embed)

    async def donner_recompense(self, membre: discord.Member, joueur: dict, r: dict) -> str:
        if r.get("recompense_or"):
            frais = await self.pb.requete("GET", f'/api/collections/joueurs/records/{joueur["id"]}')
            await self.pb.maj("joueurs", joueur["id"], {"eco_or": (frais.get("eco_or") or 0) + r["recompense_or"]})
            await self.pb.creer("eco_gains", {"joueur": joueur["id"], "commande": f'niveau {r.get("niveau")}', "monnaie": "Or",
                                              "montant": r["recompense_or"], "points_jauge": 0,
                                              "date": _pb_date(datetime.now(timezone.utc)), "origine": ""})
        if r.get("objet"):
            await self.ajouter_objet(joueur["id"], r["objet"], 1, "recompense_niveau", f'Niveau {r.get("niveau")}')
        if r.get("recompense_role"):
            await self.donner_role(membre, joueur, None, r["recompense_role"], r.get("role_duree_jours") or 0)
        if r.get("succes"):
            await self.donner_succes(joueur, r["succes"])
        return r.get("libelle") or r.get("recompense_role") or r.get("recompense_objet") or ""

    async def donner_role(self, membre: discord.Member, joueur: dict | None, role_id: str | None, role_nom: str | None, jours: float) -> str | None:
        """Donne le rôle ; renvoie None si c'est fait, sinon la raison de l'échec (rôle absent, permission manquante)."""
        role = self._role_discord(membre.guild, role_id, role_nom)
        if not role:
            log.warning("Rôle introuvable sur le serveur : %s", role_nom or role_id)
            return f"le rôle « {role_nom or role_id} » n'existe pas sur ce serveur"
        try:
            await membre.add_roles(role, reason="Économie CDT")
        except discord.HTTPException as exc:
            log.warning("Impossible de donner le rôle %s : %s", role.name, exc)
            return f"je n'ai pas la permission de donner le rôle « {role.name} » (mon rôle doit être au-dessus, avec « Gérer les rôles »)"
        if jours:
            expire = datetime.now(timezone.utc) + timedelta(days=jours)
            await self.pb.creer("eco_roles_temporaires", {
                "joueur": joueur["id"] if joueur else "", "discord_user_id": str(membre.id), "guild_id": str(membre.guild.id),
                "role_id": str(role.id), "role_nom": role.name, "expire_le": _pb_date(expire),
            })

    async def donner_succes(self, joueur: dict, succes_id: str | None):
        """Ajoute le membre du site (joueurs.joueur) aux détenteurs du succès."""
        if not succes_id or not joueur.get("joueur"):
            return
        s = await self.pb.requete("GET", f"/api/collections/succes/records/{succes_id}")
        membres = s.get("members") or []
        if joueur["joueur"] not in membres:
            await self.pb.maj("succes", succes_id, {"members": membres + [joueur["joueur"]]})

    async def ajouter_objet(self, joueur_id: str, objet_id: str, quantite: int, motif: str, note: str = ""):
        ligne = await self.pb.premier("eco_inventaire", f'joueur="{echapper(joueur_id)}" && objet="{echapper(objet_id)}"')
        if ligne:
            await self.pb.maj("eco_inventaire", ligne["id"], {"quantite": (ligne.get("quantite") or 0) + quantite})
        else:
            await self.pb.creer("eco_inventaire", {"joueur": joueur_id, "objet": objet_id, "quantite": quantite})
        await self.tracer_mouvement(joueur_id, objet_id, quantite, motif, note)

    # Motifs connus de la base ; un motif plus récent retombe sur le plus proche si la base le refuse.
    MOTIFS_REPLI = {"vente_receleur": "vente", "achat_receleur": "achat", "drop": "don", "drop_annule": "don",
                    "drop_ramasse": "don", "loterie": "achat", "rostheim": "achat", "boite_a_role_rendue": "utilisation"}

    async def tracer_mouvement(self, joueur_id: str, objet_id: str, quantite: int, motif: str, note: str = ""):
        """Historique eco_mouvements. Ne doit jamais interrompre l'opération (objet déjà retiré ou ajouté) :
        si la base refuse le motif, on réessaie avec un motif connu (le vrai motif passe dans la note), puis on abandonne."""
        ligne = {"joueur": joueur_id, "objet": objet_id, "quantite": quantite, "motif": motif, "note": note}
        try:
            await self.pb.creer("eco_mouvements", ligne)
            return
        except Exception as exc:
            log.warning("Mouvement refusé (motif %s) : %s", motif, exc)
        repli = self.MOTIFS_REPLI.get(motif)
        if repli:
            try:
                await self.pb.creer("eco_mouvements", {**ligne, "motif": repli, "note": f"{motif} · {note}".strip(" ·")})
                return
            except Exception:
                log.exception("Mouvement refusé même avec le motif %s", repli)

    async def boucle_roles_temporaires(self):
        """Retire les rôles temporaires arrivés à échéance (toutes les 10 minutes)."""
        await self.client.wait_until_ready()
        while not self.client.is_closed():
            try:
                for t in await self.pb.lister("eco_roles_temporaires", f'expire_le<="{_pb_date(datetime.now(timezone.utc))}"'):
                    guild = self.client.get_guild(int(t["guild_id"])) if t.get("guild_id") else None
                    membre = guild.get_member(int(t["discord_user_id"])) if guild else None
                    role = guild.get_role(int(t["role_id"])) if guild and t.get("role_id") else None
                    if membre and role:
                        await membre.remove_roles(role, reason="Fin du rôle temporaire (économie CDT)")
                    await self.pb.supprimer("eco_roles_temporaires", t["id"])
            except Exception:
                log.exception("Erreur dans la boucle des rôles temporaires")
            await asyncio.sleep(600)

    # ------------------------------------------------------------ commandes ??

    async def commande(self, message: discord.Message):
        # Commandes Rostheim et RP (définies dans la base) d'abord, puis commandes de l'économie.
        if await self.rostheim.traiter(message):
            return
        if not message.content.startswith(self.prefixe):
            return
        nom, _, arg = message.content[len(self.prefixe):].strip().partition(" ")
        actions = {
            "niveau": self.cmd_niveau, "rang": self.cmd_niveau, "argent": self.cmd_argent,
            "classement": self.cmd_classement, "topniveau": self.cmd_classement, "topargent": self.cmd_topargent,
            "inventaire": self.cmd_inventaire, "boutique": self.cmd_boutique, "acheter": self.cmd_acheter,
        }
        action = actions.get(nom.lower())
        if action:
            await action(message, arg.strip())

    async def _carte(self, message: discord.Message, genre: str):
        from app import eco_vues
        cfg = await self.config()
        cible = message.mentions[0] if message.mentions else message.author
        if cible != message.author and not cfg["reglages"].get("voir_niveau_autres"):
            return
        embed, image = await (eco_vues.carte_argent if genre == "argent" else eco_vues.carte_niveau)(self, cible)
        if not image:
            await message.reply(embed=discord.Embed.from_dict(embed), mention_author=False)
            return
        import io
        await message.reply(embed=discord.Embed.from_dict(embed), file=discord.File(io.BytesIO(image), "carte.png"), mention_author=False)

    async def cmd_niveau(self, message: discord.Message, arg: str):
        await self._carte(message, "niveau")

    async def cmd_argent(self, message: discord.Message, arg: str):
        await self._carte(message, "argent")

    async def _top(self, message: discord.Message, genre: str):
        from app import eco_vues
        embed, comp = await eco_vues.vue_classement(self, genre)
        vue = eco_vues.vue_discord(comp)
        await message.reply(embed=discord.Embed.from_dict(embed), mention_author=False, **({"view": vue} if vue else {}))

    async def cmd_classement(self, message: discord.Message, arg: str):
        await self._top(message, "niveau")

    async def cmd_topargent(self, message: discord.Message, arg: str):
        await self._top(message, "argent")

    async def cmd_inventaire(self, message: discord.Message, arg: str):
        from app import eco_vues
        embed, comp = await eco_vues.vue_inventaire(self, message.author)
        vue = eco_vues.vue_discord(comp)
        await message.reply(embed=discord.Embed.from_dict(embed), mention_author=False, **({"view": vue} if vue else {}))

    def _boutiques_accessibles(self, cfg: dict, membre: discord.Member) -> list[dict]:
        def roles(txt):
            return [r.strip().lstrip("@") for r in (txt or "").split(",") if r.strip()]
        sortie = []
        for b in cfg["boutiques"]:
            autorises, interdits = roles(b.get("roles_autorises")), roles(b.get("roles_interdits"))
            a_role = lambda nom: any(str(r.id) == nom or meme_nom(r.name, nom) for r in membre.roles)
            if autorises and not any(a_role(r) for r in autorises):
                continue
            if any(a_role(r) for r in interdits):
                continue
            sortie.append(b)
        return sortie

    def _articles(self, cfg: dict, boutique: dict) -> list[dict]:
        l = [a for a in cfg["articles"] if a.get("boutique") == boutique["id"]]
        cles = {
            "prix_asc": lambda a: (a.get("prix") or 0), "prix_desc": lambda a: -(a.get("prix") or 0),
            "nom": lambda a: (a.get("nom") or "").lower(),
        }
        return sorted(l, key=cles.get(boutique.get("tri"), lambda a: (a.get("ordre") or 0, a.get("prix") or 0)))

    async def cmd_boutique(self, message: discord.Message, arg: str):
        await message.reply("Les boutiques s'ouvrent avec la commande **/boutique** (pages, tri, achat par bouton).", mention_author=False)

    async def cmd_acheter(self, message: discord.Message, arg: str):
        cfg = await self.config()
        if not arg:
            await message.reply("Les achats se font avec la commande **/boutique** (bouton « Acheter »).", mention_author=False)
            return
        # Numéro : dans la première boutique accessible qui le contient (même ordre que la boutique) ; sinon par nom.
        trouve = None
        for b in self._boutiques_accessibles(cfg, message.author):
            arts = self._articles(cfg, b)
            if arg.isdigit() and 1 <= int(arg) <= len(arts):
                trouve = (b, arts[int(arg) - 1])
                break
            a = next((a for a in arts if a["nom"].lower() == arg.lower()), None)
            if a:
                trouve = (b, a)
                break
        if not trouve:
            await message.reply("Article introuvable.", mention_author=False)
            return
        from app import eco_vues
        ok, texte = await self.acheter(message.author, trouve[0], trouve[1], message.jump_url)
        await message.reply(embed=discord.Embed.from_dict(eco_vues.resultat_achat(ok, texte)), mention_author=False)

    async def acheter(self, membre: discord.Member, boutique: dict, article: dict, origine: str, quantite: int = 1) -> tuple[bool, str]:
        """Achat d'un article (commande à préfixe, bouton ou fenêtre de quantité) : (réussi, texte à afficher)."""
        cfg = await self.config()
        if not any(b["id"] == boutique["id"] for b in self._boutiques_accessibles(cfg, membre)):
            return False, "Cette boutique ne t'est pas accessible."
        async with self._verrou(membre.id):
            joueur = await self.joueur_de(membre)
            if not joueur:
                return False, "Aucune fiche joueur liée à ce compte Discord."
            article = await self.pb.requete("GET", f'/api/collections/eco_articles/records/{article["id"]}')  # stock à jour
            est_role = article.get("type") in ("role_permanent", "role_temporaire") and (article.get("role_id") or article.get("role_nom"))
            if est_role:
                quantite = 1  # un rôle ne s'achète qu'une fois
                role = self._role_discord(membre.guild, article.get("role_id"), article.get("role_nom"))
                if role and role in membre.roles:
                    return False, f"❌ Vous possédez déjà le rôle {role.mention}."
            quantite = max(1, int(quantite or 1))
            prix_unitaire = article.get("prix") or 0
            prix = prix_unitaire * quantite
            if not article.get("actif"):
                return False, "Cet article n'est plus en vente."
            if not article.get("stock_illimite") and (article.get("stock") or 0) < quantite:
                return False, "Cet article est épuisé." if (article.get("stock") or 0) <= 0 else f'Il n\'en reste que {article.get("stock")}.'
            if (joueur.get("eco_or") or 0) < prix:
                return False, f'Il te manque {prix - (joueur.get("eco_or") or 0)} Or.'
            # Le rôle est donné AVANT de débiter : s'il ne peut pas l'être, l'achat est annulé sans rien prélever.
            if est_role:
                jours = (article.get("duree_jours") or 0) if article.get("type") == "role_temporaire" else 0
                echec = await self.donner_role(membre, joueur, article.get("role_id"), article.get("role_nom"), jours)
                if echec:
                    return False, f"Achat annulé, rien n'a été prélevé : {echec}."
            depense_avant = joueur.get("eco_or_depense") or 0
            joueur = await self.pb.maj("joueurs", joueur["id"], {"eco_or": joueur["eco_or"] - prix, "eco_or_depense": depense_avant + prix})
            if not article.get("stock_illimite"):
                await self.pb.maj("eco_articles", article["id"], {"stock": (article.get("stock") or 0) - quantite})
                self._charge_a = 0  # stock affiché à jour au prochain affichage
            if article.get("objet"):
                await self.ajouter_objet(joueur["id"], article["objet"], quantite, "achat", article["nom"])
            await self.pb.creer("eco_achats", {
                "joueur": joueur["id"], "article": article["id"], "article_nom": article["nom"] + (f" ×{quantite}" if quantite > 1 else ""), "prix": prix,
                "date": _pb_date(datetime.now(timezone.utc)), "origine": origine,
            })
            if boutique.get("retirer_roles_acces"):
                for nom in [r.strip().lstrip("@") for r in (boutique.get("roles_autorises") or "").split(",") if r.strip()]:
                    role = next((r for r in membre.roles if str(r.id) == nom or meme_nom(r.name, nom)), None)
                    if role:
                        try:
                            await membre.remove_roles(role, reason="Achat dans une boutique à accès limité")
                        except discord.HTTPException as exc:
                            log.warning("Impossible de retirer le rôle %s : %s", role.name, exc)
            for p in cfg["paliers"]:
                if p.get("declencheur") == "or_depense" and depense_avant < (p.get("seuil") or 0) <= depense_avant + prix:
                    await self.donner_succes(joueur, p.get("succes"))
        from app import eco_vues
        return True, f'{article.get("emoji") or ""} **{article["nom"]}**{f" ×{quantite}" if quantite > 1 else ""} pour **{eco_vues._n(prix)}** {eco_vues.or_txt(cfg)}.\nIl te reste **{eco_vues._n(joueur["eco_or"])}** {eco_vues.or_txt(cfg)}.'.strip()
