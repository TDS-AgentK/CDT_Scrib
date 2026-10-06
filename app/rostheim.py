"""Événement Rostheim et commande « après un RP », pilotés par la base du site :
- ros_commandes : chaque commande (??texte, ??gold-faveur, ??dépenser-sachoir, ??boîte-à-rôle…) avec son domaine,
  son type, ses gains, son coût, le rôle requis, le salon autorisé et ses limites ;
- ros_domaines / ros_paliers / ros_soldes / ros_recompenses : monnaies, jauges, paliers et boutiques de domaine ;
- eco_commande_rp : récompense de la commande RP (Or, XP, rôle, pour le lanceur et ses partenaires mentionnés).
Les textes de jeu (embeds de palier, message RP) viennent tous de la base ; un texte vide n'est pas envoyé.
Chaque gain est tracé dans eco_gains.
"""
import logging
from datetime import datetime, timedelta, timezone

import discord

from app.pocketbase import echapper

log = logging.getLogger("cdt_scrib.rostheim")


def _pb_date(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")


def _nombre(n) -> str:
    return f"{n:,}".replace(",", " ")


class Rostheim:
    def __init__(self, eco):
        self.eco = eco
        self.pb = eco.pb

    async def _donnees(self) -> dict:
        reglages = (await self.eco.config())["reglages"]
        commandes, domaines, rp = await self._lire()
        return {"reglages": reglages, "commandes": commandes, "domaines": domaines, "rp": rp}

    async def _lire(self):
        import asyncio
        return await asyncio.gather(
            self.pb.lister("ros_commandes", "actif=true"), self.pb.lister("ros_domaines", tri="ordre"),
            self.pb.premier("eco_commande_rp", 'cle="rp"'),
        )

    async def traiter(self, message: discord.Message) -> bool:
        """Traite le message s'il s'agit d'une commande Rostheim ou RP ; renvoie True si c'en était une."""
        mot, _, arg = message.content.strip().partition(" ")
        mot = mot.lower()
        d = await self._donnees()
        rp = d["rp"]
        if rp and rp.get("actif") and mot == f'{self.eco.prefixe}{(rp.get("commande") or "rp").lower()}':
            await self.commande_rp(message, rp)
            return True
        cmd = next((c for c in d["commandes"] if (c.get("commande") or "").lower() == mot), None)
        if not cmd:
            return False
        if not d["reglages"].get("rostheim_actif"):
            await message.reply("L'événement Rostheim n'est pas actif.", mention_author=False)
            return True
        domaine = next((x for x in d["domaines"] if x["id"] == cmd.get("domaine")), None)
        joueur = await self._autorise(message, cmd, domaine)
        if not joueur:
            return True
        type_ = cmd.get("type")
        if type_ == "gain":
            await self.gain(message, joueur, cmd, domaine)
        elif type_ in ("conversion_or", "conversion_xp"):
            await self.conversion(message, joueur, cmd, domaine)
        elif type_ == "boutique":
            await self.boutique(message, joueur, cmd, domaine, arg.strip())
        elif type_ == "boite_a_role":
            await self.boite_a_role(message, joueur, cmd, d["domaines"], arg.strip())
        else:
            log.info("Commande Rostheim « %s » (type %s) : rien à faire côté bot pour l'instant.", cmd.get("commande"), type_)
        return True

    # ------------------------------------------------------------ contrôles

    async def _autorise(self, message: discord.Message, cmd: dict, domaine: dict | None) -> dict | None:
        """Rôle requis, salon autorisé, fiche joueur et limites ; renvoie la fiche joueur si tout est bon."""
        membre = message.author
        role = (cmd.get("role_requis") or "").strip()
        if role and not any(r.name.lower() == role.lower() or r.name.lower().startswith(role.lower() + " ") for r in membre.roles):
            await message.reply(f"Cette commande demande le rôle {role}.", mention_author=False)
            return None
        salon = (cmd.get("salon_autorise") or "").strip()
        if salon:
            ch = message.channel.parent if isinstance(message.channel, discord.Thread) else message.channel
            ids = {str(domaine.get("salon_id"))} if domaine and domaine.get("salon_id") and domaine.get("salon_nom") == salon else set()
            if str(ch.id) not in ids and ch.name != salon:
                await message.reply(f"Cette commande se lance dans le salon #{salon}.", mention_author=False)
                return None
        joueur = await self.eco.joueur_de(membre)
        if not joueur:
            await message.reply("Aucune fiche joueur liée à ce compte Discord.", mention_author=False)
            return None
        for nombre, heures in ((cmd.get("limite_nombre"), cmd.get("limite_periode_h")), (cmd.get("limite2_nombre"), cmd.get("limite2_periode_h"))):
            if not nombre or not heures:
                continue
            depuis = _pb_date(datetime.now(timezone.utc) - timedelta(hours=heures))
            deja = await self.pb.lister("eco_gains", f'joueur="{echapper(joueur["id"])}" && commande="{echapper(cmd["commande"])}" && created>="{depuis}"')
            if len(deja) >= nombre:
                await message.reply(f"Limite atteinte : {nombre} fois par {heures:g} h.", mention_author=False)
                return None
        return joueur

    async def _solde(self, joueur: dict, domaine: dict) -> dict:
        s = await self.pb.premier("ros_soldes", f'joueur="{echapper(joueur["id"])}" && domaine="{echapper(domaine["id"])}"')
        return s or await self.pb.creer("ros_soldes", {"joueur": joueur["id"], "domaine": domaine["id"], "monnaie": 0, "jauge_individuelle": 0})

    async def _tracer(self, joueur: dict, cmd: dict | None, nom: str, monnaie: str, montant, jauge, message: discord.Message):
        await self.pb.creer("eco_gains", {
            "joueur": joueur["id"], "commande": nom, "commande_rostheim": cmd["id"] if cmd else "", "monnaie": monnaie,
            "montant": montant, "points_jauge": jauge or 0, "date": _pb_date(datetime.now(timezone.utc)), "origine": message.jump_url,
        })

    # ------------------------------------------------------------ actions

    async def gain(self, message: discord.Message, joueur: dict, cmd: dict, domaine: dict):
        async with self.eco._verrou(message.author.id):
            solde = await self._solde(joueur, domaine)
            gain, jauge = cmd.get("gain_monnaie") or 0, cmd.get("points_jauge") or 0
            await self.pb.maj("ros_soldes", solde["id"], {
                "monnaie": (solde.get("monnaie") or 0) + gain, "jauge_individuelle": (solde.get("jauge_individuelle") or 0) + jauge,
            })
            domaine = await self.pb.requete("GET", f'/api/collections/ros_domaines/records/{domaine["id"]}')
            avant = domaine.get("jauge_collective") or 0
            domaine = await self.pb.maj("ros_domaines", domaine["id"], {"jauge_collective": avant + jauge})
            await self._tracer(joueur, cmd, cmd["commande"], domaine.get("monnaie_nom") or "", gain, jauge, message)
        await message.reply(
            f'+{gain} {domaine.get("monnaie_emoji") or ""} {domaine.get("monnaie_nom")} · jauge {domaine.get("nom")} : {_nombre(domaine["jauge_collective"])} pts'.replace("  ", " "),
            mention_author=False)
        await self._paliers(message.channel, domaine, avant, domaine["jauge_collective"])

    async def _paliers(self, channel, domaine: dict, avant: int, apres: int):
        """Passage de palier de la jauge collective : palier marqué atteint, embed du palier envoyé s'il est rempli."""
        paliers = await self.pb.lister("ros_paliers", f'domaine="{echapper(domaine["id"])}"', tri="niveau")
        for p in paliers:
            if avant < (p.get("points") or 0) <= apres:
                await self.pb.maj("ros_paliers", p["id"], {"atteint": True})
                await self.pb.maj("ros_domaines", domaine["id"], {"palier_actuel": p.get("niveau")})
                texte = (p.get("message_embed") or "").strip()
                if texte:
                    await channel.send(embed=discord.Embed(description=texte[:4096], color=0xC5A24F))

    async def conversion(self, message: discord.Message, joueur: dict, cmd: dict, domaine: dict):
        cout = cmd.get("cout_monnaie") or 0
        async with self.eco._verrou(message.author.id):
            solde = await self._solde(joueur, domaine)
            if (solde.get("monnaie") or 0) < cout:
                await message.reply(f'Il faut {cout} {domaine.get("monnaie_nom")} (tu en as {solde.get("monnaie") or 0}).', mention_author=False)
                return
            await self.pb.maj("ros_soldes", solde["id"], {"monnaie": solde["monnaie"] - cout})
            frais = await self.pb.requete("GET", f'/api/collections/joueurs/records/{joueur["id"]}')
            if cmd["type"] == "conversion_or":
                await self.pb.maj("joueurs", joueur["id"], {"eco_or": (frais.get("eco_or") or 0) + (cmd.get("gain_or") or 0)})
                gain_txt, ancien_xp = f'+{cmd.get("gain_or") or 0} Or', None
            else:
                ancien_xp = frais.get("eco_xp") or 0
                frais = await self.pb.maj("joueurs", joueur["id"], {"eco_xp": ancien_xp + (cmd.get("gain_xp") or 0)})
                gain_txt = f'+{cmd.get("gain_xp") or 0} XP'
            await self._tracer(joueur, cmd, cmd["commande"], domaine.get("monnaie_nom") or "", -cout, 0, message)
        await message.reply(f'{gain_txt} · −{cout} {domaine.get("monnaie_nom")}', mention_author=False)
        if ancien_xp is not None:
            cfg = await self.eco.config()
            from app.economie import niveau_de
            n0, n1 = niveau_de(cfg["courbe"], ancien_xp)[0], niveau_de(cfg["courbe"], frais["eco_xp"])[0]
            if n1 > n0:
                await self.eco.monter_niveau(message.channel, message.author, frais, n0, n1)

    async def _recompenses(self, domaine: dict | None) -> list[dict]:
        filtre = f'actif=true && domaine="{echapper(domaine["id"])}"' if domaine else "actif=true && toute_monnaie=true"
        return await self.pb.lister("ros_recompenses", filtre, tri="ordre")

    async def boutique(self, message: discord.Message, joueur: dict, cmd: dict, domaine: dict, arg: str):
        """??dépenser-<monnaie> : affiche la boutique du domaine ; avec un numéro, achète l'article."""
        articles = await self._recompenses(domaine) + await self._recompenses(None)
        if not arg:
            lignes = [f'`{i}` **{a["libelle"]}** — {a.get("prix") or 0} {"(toute monnaie)" if a.get("toute_monnaie") else domaine.get("monnaie_nom")} · {a.get("portee")}'
                      for i, a in enumerate(articles, 1)]
            embed = discord.Embed(title=f'{domaine.get("nom")} — {domaine.get("monnaie_nom")}', description="\n".join(lignes)[:4096] or "—", color=0xC5A24F)
            embed.set_footer(text=f'{cmd["commande"]} <numéro>')
            await message.channel.send(embed=embed)
            return
        if not arg.isdigit() or not 1 <= int(arg) <= len(articles):
            await message.reply("Numéro d'article inconnu.", mention_author=False)
            return
        await self._acheter(message, joueur, domaine, articles[int(arg) - 1], cmd)

    async def _acheter(self, message: discord.Message, joueur: dict, domaine: dict, article: dict, cmd: dict):
        prix = article.get("prix") or 0
        async with self.eco._verrou(message.author.id):
            solde = await self._solde(joueur, domaine)
            if (solde.get("monnaie") or 0) < prix:
                await message.reply(f'Il faut {prix} {domaine.get("monnaie_nom")} (tu en as {solde.get("monnaie") or 0}).', mention_author=False)
                return
            await self.pb.maj("ros_soldes", solde["id"], {"monnaie": solde["monnaie"] - prix})
            if article.get("objet"):
                await self.eco.ajouter_objet(joueur["id"], article["objet"], 1, "rostheim", article["libelle"])
            role = article["libelle"][len("Obtenir le rôle "):].strip() if article["libelle"].startswith("Obtenir le rôle ") else ""
            if role:
                await self.eco.donner_role(message.author, joueur, None, role, 0)
            await self._tracer(joueur, cmd, f'{cmd["commande"]} : {article["libelle"]}', domaine.get("monnaie_nom") or "", -prix, 0, message)
        await message.reply(f'Acheté : **{article["libelle"]}** pour {prix} {domaine.get("monnaie_nom")}.', mention_author=False)

    async def boite_a_role(self, message: discord.Message, joueur: dict, cmd: dict, domaines: list[dict], arg: str):
        """Rôles des boutiques de domaine (« Obtenir le rôle … »), chacun payé dans la monnaie de son domaine."""
        offres = []
        for d in domaines:
            for a in await self._recompenses(d):
                if a["libelle"].startswith("Obtenir le rôle "):
                    offres.append((d, a))
        if not arg:
            lignes = [f'`{i}` {a["libelle"][len("Obtenir le rôle "):]} — {a.get("prix") or 0} {d.get("monnaie_nom")}' for i, (d, a) in enumerate(offres, 1)]
            embed = discord.Embed(title="Boîte à rôles", description="\n".join(lignes) or "—", color=0xC5A24F)
            embed.set_footer(text=f'{cmd["commande"]} <numéro>')
            await message.channel.send(embed=embed)
            return
        if not arg.isdigit() or not 1 <= int(arg) <= len(offres):
            await message.reply("Numéro inconnu.", mention_author=False)
            return
        d, a = offres[int(arg) - 1]
        await self._acheter(message, joueur, d, a, cmd)

    # ------------------------------------------------------------ commande RP

    async def commande_rp(self, message: discord.Message, rp: dict):
        membres = [m for m in message.mentions if isinstance(m, discord.Member) and not m.bot and m.id != message.author.id]
        maxi = rp.get("max_mentions") or 0
        if maxi and len(membres) > maxi:
            await message.reply(f"{maxi} partenaires au maximum.", mention_author=False)
            return
        concernes = []
        for m in [message.author] + membres:
            lanceur = m.id == message.author.id
            joueur = await self.eco.joueur_de(m)
            maj = {}
            if joueur:
                nom_cmd = f'{self.eco.prefixe}{rp.get("commande") or "rp"}'
                async with self.eco._verrou(m.id):
                    frais = await self.pb.requete("GET", f'/api/collections/joueurs/records/{joueur["id"]}')
                    ancien_xp = frais.get("eco_xp") or 0
                    if rp.get("or_montant") and (lanceur or rp.get("or_mentionnes")):
                        maj["eco_or"] = (frais.get("eco_or") or 0) + rp["or_montant"]
                    if rp.get("xp_montant") and (lanceur or rp.get("xp_mentionnes")):
                        maj["eco_xp"] = ancien_xp + rp["xp_montant"]
                    if maj:
                        frais = await self.pb.maj("joueurs", joueur["id"], maj)
                    if "eco_or" in maj:
                        await self._tracer(joueur, None, nom_cmd, "Or", rp["or_montant"], 0, message)
                    if "eco_xp" in maj:
                        await self._tracer(joueur, None, nom_cmd, "XP", rp["xp_montant"], 0, message)
                if "eco_xp" in maj:
                    cfg = await self.eco.config()
                    from app.economie import niveau_de
                    n0, n1 = niveau_de(cfg["courbe"], ancien_xp)[0], niveau_de(cfg["courbe"], frais["eco_xp"])[0]
                    if n1 > n0:
                        await self.eco.monter_niveau(message.channel, m, frais, n0, n1)
            if (rp.get("role_nom") or rp.get("role_id")) and (lanceur or rp.get("role_mentionnes")):
                await self.eco.donner_role(m, joueur, rp.get("role_id"), rp.get("role_nom"), 0)
            concernes.append(m)
        texte = (rp.get("message_texte") or "").strip()
        if rp.get("message_actif") and texte:
            salon = None
            if rp.get("message_salon_id") and str(rp["message_salon_id"]).isdigit():
                salon = message.guild.get_channel(int(rp["message_salon_id"]))
            if not salon and rp.get("message_salon_nom"):
                cible = rp["message_salon_nom"].lower()
                salon = next((c for c in message.guild.text_channels if c.name.lower() == cible), None)
            await (salon or message.channel).send(texte.replace("{args.all}", ", ".join(m.mention for m in concernes)))
