"""Événement Rostheim et commande « après un RP », pilotés par la base du site :
- ros_commandes : chaque commande (??texte, ??gold-faveur, ??dépenser-sachoir, ??boîte-à-rôle…) avec son domaine,
  son type, ses gains, son coût, le rôle requis, le salon autorisé et ses limites ;
- ros_domaines / ros_paliers / ros_soldes / ros_recompenses : monnaies, jauges, paliers et boutiques de domaine ;
- eco_commande_rp : récompense de la commande RP (Or, XP, rôle, pour le lanceur et ses partenaires mentionnés).
Les textes de jeu (embeds de palier, message RP) viennent tous de la base ; un texte vide n'est pas envoyé.
Embed de palier : rien n'est posté automatiquement au passage d'un palier (le palier est seulement marqué atteint).
K l'envoie elle-même depuis l'éditeur du site (« Envoyer dans le salon ») : le site ajoute une demande dans ros_envois,
boucle_envois() la traite — embed complet (message_embed_json) avec l'auteur du domaine, posté dans le salon du domaine
sous le nom et l'avatar des réglages (« Conseiller Corvoline ») — et note le résultat (lien du message ou erreur).
Chaque gain est tracé dans eco_gains.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import discord

from app.economie import meme_nom, normaliser
from app.pocketbase import echapper

log = logging.getLogger("cdt_scrib.rostheim")


VERIFIER_ENVOIS_S = 15


def embed_palier(palier: dict, domaine: dict) -> discord.Embed | None:
    """Embed complet enregistré pour le palier ; l'auteur est toujours celui du domaine (verrouillé sur le site)."""
    brut = (palier.get("message_embed_json") or "").strip()
    if not brut:
        return None
    try:
        donnees = json.loads(brut)
    except ValueError:
        log.warning("Embed du palier %s illisible (JSON)", palier.get("id"))
        return None
    if domaine.get("embed_auteur_nom"):
        donnees["author"] = {"name": domaine["embed_auteur_nom"]}
        if domaine.get("embed_auteur_icone"):
            donnees["author"]["icon_url"] = domaine["embed_auteur_icone"]
    return discord.Embed.from_dict(donnees)


def _pb_date(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")


def _nombre(n) -> str:
    return f"{n:,}".replace(",", " ")


class Rostheim:
    def __init__(self, eco):
        self.eco = eco
        self.pb = eco.pb

    def _nom(self, cmd: dict) -> str:
        """Nom de la commande tel qu'il se tape : le « ?? » enregistré dans la base suit le préfixe du bot (ECO_PREFIX),
        et le « -- » aussi quand un préfixe autre que « ?? » est choisi (--wut devient !!wut)."""
        nom = cmd.get("commande") or ""
        if nom.startswith("??") or (nom.startswith("--") and self.eco.prefixe != "??"):
            return self.eco.prefixe + nom[2:]
        return nom

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
        cmd = next((c for c in d["commandes"] if self._nom(c).lower() == mot), None)
        if not cmd:
            return False
        if not d["reglages"].get("rostheim_actif"):
            await message.reply("L'événement Rostheim n'est pas actif.", mention_author=False)
            return True
        domaine = next((x for x in d["domaines"] if x["id"] == cmd.get("domaine")), None)
        if cmd.get("type") == "outil" and (cmd.get("reponses") or "").strip():
            # Ex. --wut : une réponse tirée au hasard, sans fiche joueur ni gain.
            if await self._lieu_et_role(message, cmd, domaine):
                await self.reponse_au_hasard(message, cmd)
            return True
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

    async def _lieu_et_role(self, message: discord.Message, cmd: dict, domaine: dict | None) -> bool:
        """Rôle requis et salon autorisé de la commande."""
        membre = message.author
        role = (cmd.get("role_requis") or "").strip()
        # « Roi » accepte aussi un rôle « Roi / Reine » (nom qui commence par le rôle demandé).
        if role and not any(meme_nom(r.name, role) or normaliser(r.name).startswith(normaliser(role)) for r in membre.roles):
            await message.reply(f"Cette commande demande le rôle {role}.", mention_author=False)
            return False
        salon = (cmd.get("salon_autorise") or "").strip()
        if salon:
            ch = message.channel.parent if isinstance(message.channel, discord.Thread) else message.channel
            ids = {str(domaine.get("salon_id"))} if domaine and domaine.get("salon_id") and domaine.get("salon_nom") == salon else set()
            if str(ch.id) not in ids and not meme_nom(ch.name, salon):
                await message.reply(f"Cette commande se lance dans le salon #{salon}.", mention_author=False)
                return False
        return True

    async def _autorise(self, message: discord.Message, cmd: dict, domaine: dict | None) -> dict | None:
        """Rôle requis, salon autorisé, fiche joueur et limites ; renvoie la fiche joueur si tout est bon."""
        if not await self._lieu_et_role(message, cmd, domaine):
            return None
        joueur = await self.eco.joueur_de(message.author)
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

    async def reponse_au_hasard(self, message: discord.Message, cmd: dict):
        """Envoie une des réponses de la commande (une par ligne), et supprime le message déclencheur si demandé."""
        import random
        reponses = [l for l in (cmd.get("reponses") or "").splitlines() if l.strip()]
        await message.channel.send(random.choice(reponses))
        if cmd.get("supprimer_declencheur"):
            try:
                await message.delete()
            except discord.HTTPException:
                log.warning("Impossible de supprimer le message de commande (permission « Gérer les messages » ?)")

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
        """Passage de palier de la jauge collective : palier marqué atteint (l'embed est envoyé à la main depuis le site)."""
        paliers = await self.pb.lister("ros_paliers", f'domaine="{echapper(domaine["id"])}"', tri="niveau")
        for p in paliers:
            if avant < (p.get("points") or 0) <= apres:
                await self.pb.maj("ros_paliers", p["id"], {"atteint": True})
                await self.pb.maj("ros_domaines", domaine["id"], {"palier_actuel": p.get("niveau")})

    def _salon(self, domaine: dict):
        """Salon du domaine : par son ID s'il est connu, sinon par son nom sur le serveur."""
        client = self.eco.client
        if domaine.get("salon_id"):
            salon = client.get_channel(int(domaine["salon_id"]))
            if salon:
                return salon
        for guild in client.guilds:
            if self.eco.guild_id and guild.id != self.eco.guild_id:
                continue
            for salon in guild.text_channels:
                if meme_nom(salon.name, domaine.get("salon_nom")):
                    return salon
        return None

    async def _envoyer_palier(self, channel, embed: discord.Embed):
        """Embed de palier posté sous le nom et l'avatar des réglages (webhook du salon), sinon par le bot lui-même ;
        renvoie le message posté."""
        from app.anniversaires import _webhook
        reglages = (await self.eco.config())["reglages"]
        nom, avatar = reglages.get("rostheim_envoi_nom") or "", reglages.get("rostheim_envoi_avatar") or ""
        webhook = await _webhook(self.eco.client, channel) if (nom or avatar) and isinstance(channel, discord.TextChannel) else None
        if webhook:
            return await webhook.send(embed=embed, username=nom or None, avatar_url=avatar or None, wait=True)
        return await channel.send(embed=embed)

    async def traiter_envoi(self, demande: dict):
        """Une demande d'envoi (ros_envois) : embed du palier posté dans le salon du domaine, résultat noté."""
        maj = {"statut": "erreur"}
        try:
            palier = await self.pb.requete("GET", f'/api/collections/ros_paliers/records/{demande["palier"]}')
            domaine = await self.pb.requete("GET", f'/api/collections/ros_domaines/records/{palier["domaine"]}')
            embed = embed_palier(palier, domaine)
            salon = self._salon(domaine)
            if embed is None:
                maj["erreur"] = "embed vide ou illisible"
            elif salon is None:
                maj["erreur"] = f'salon #{domaine.get("salon_nom") or "?"} introuvable'
            else:
                message = await self._envoyer_palier(salon, embed)
                maj = {"statut": "envoye", "envoye_le": _pb_date(datetime.now(timezone.utc)), "lien": getattr(message, "jump_url", "") or "", "erreur": ""}
        except discord.HTTPException as e:
            maj["erreur"] = f"Discord : {e.text or e}"[:300]
        except Exception as e:  # noqa: BLE001 — l'erreur est notée sur la demande, visible dans l'admin
            log.exception("Envoi de l'embed de palier impossible")
            maj["erreur"] = str(e)[:300]
        await self.pb.maj("ros_envois", demande["id"], maj)

    async def boucle_envois(self):
        """Traite les demandes d'envoi d'embeds de palier faites depuis le site (toutes les VERIFIER_ENVOIS_S secondes)."""
        await self.eco.client.wait_until_ready()
        while not self.eco.client.is_closed():
            try:
                for demande in await self.pb.lister("ros_envois", 'statut="en_attente"', tri="created"):
                    await self.traiter_envoi(demande)
            except Exception:
                log.exception("Erreur dans la boucle des envois d'embeds de palier")
            await asyncio.sleep(VERIFIER_ENVOIS_S)

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
            embed.set_footer(text=f'{self._nom(cmd)} <numéro>')
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
            # Rôle donné avant de débiter : en cas d'échec, rien n'est prélevé.
            role = article["libelle"][len("Obtenir le rôle "):].strip() if article["libelle"].startswith("Obtenir le rôle ") else ""
            if role:
                echec = await self.eco.donner_role(message.author, joueur, None, role, 0)
                if echec:
                    await message.reply(f"Achat annulé, rien n'a été prélevé : {echec}.", mention_author=False)
                    return
            await self.pb.maj("ros_soldes", solde["id"], {"monnaie": solde["monnaie"] - prix})
            if article.get("objet"):
                await self.eco.ajouter_objet(joueur["id"], article["objet"], 1, "rostheim", article["libelle"])
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
            embed.set_footer(text=f'{self._nom(cmd)} <numéro>')
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
        concernes, echecs = [], set()
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
                echec = await self.eco.donner_role(m, joueur, rp.get("role_id"), rp.get("role_nom"), 0)
                if echec:
                    echecs.add(echec)
            concernes.append(m)
        for echec in echecs:
            await message.reply(f"Attention : {echec}.", mention_author=False)
        texte = (rp.get("message_texte") or "").strip()
        if rp.get("message_actif") and texte:
            salon = None
            if rp.get("message_salon_id") and str(rp["message_salon_id"]).isdigit():
                salon = message.guild.get_channel(int(rp["message_salon_id"]))
            if not salon and rp.get("message_salon_nom"):
                salon = next((c for c in message.guild.text_channels if meme_nom(c.name, rp["message_salon_nom"])), None)
            await (salon or message.channel).send(texte.replace("{args.all}", ", ".join(m.mention for m in concernes)))
