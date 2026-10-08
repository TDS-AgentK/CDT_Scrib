"""Le Receleur : il reprend des objets aux joueurs (quota par semaine, du lundi au dimanche), les stocke et les
revend avec son propre stock. Réglages, prix, stocks et historique se gèrent sur le site (collections rec_*).

- rec_reglages (cle « general ») : ouvert, ouverture_texte, changement_prix_texte, ventes_par_semaine,
  message_non_repris (réponse quand un objet n'a pas de prix de reprise), notif_discord_ids (qui prévenir pour un nouvel objet),
  surveille_depuis (date : seuls les objets créés après sont suivis)
- rec_statuts : succès du site (champ succes) ou rôle Discord → quota de reprises par semaine (le plus haut gagne)
- rec_objets : objet, prix_reprise (le joueur vend), prix_vente (le joueur achète), stock, actif,
  non_revendable (case à cocher : l'objet ne peut pas être vendu au Receleur, aucun rappel de prix)
- rec_mouvements : historique (sens « reprise » = le joueur vend, « vente » = le joueur achète)
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord

from app import eco_vues
from app.eco_actions import _gain, _n, changer_or, objet_par_nom, quantite, retirer_objet
from app.pocketbase import echapper

log = logging.getLogger("cdt_scrib.receleur")
PARIS = ZoneInfo("Europe/Paris")
_verrou_stock = asyncio.Lock()  # un seul mouvement de stock à la fois


def _pb_date(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")


def debut_semaine() -> datetime:
    """Lundi 00:00 (heure de Paris) de la semaine en cours."""
    maintenant = datetime.now(PARIS)
    return (maintenant - timedelta(days=maintenant.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


async def reglages(eco) -> dict:
    return await eco.pb.premier("rec_reglages", 'cle="general"') or {}


def _ferme(r: dict) -> str | None:
    if r.get("ouvert"):
        return None
    suite = f" Ouverture : {r['ouverture_texte']}." if r.get("ouverture_texte") else ""
    return f"La boutique du Receleur n'est pas encore ouverte.{suite}"


async def quota(eco, membre: discord.Member, joueur: dict, r: dict) -> int:
    """Nombre d'objets que ce joueur peut vendre par semaine : le plus haut quota de ses statuts (succès du site détenu,
    ou rôle Discord), sinon le quota général."""
    maxi = r.get("ventes_par_semaine") or 0
    detenus = set()
    for s in await eco.pb.lister("rec_statuts", "actif=true"):
        if s.get("succes") and joueur.get("joueur"):
            if s["succes"] not in detenus:
                succes = await eco.pb.requete("GET", f'/api/collections/succes/records/{s["succes"]}')
                if joueur["joueur"] in (succes.get("members") or []):
                    detenus.add(s["succes"])
            a_le_statut = s["succes"] in detenus
        else:
            a_le_statut = bool(s.get("role_id") or s.get("role_nom")) and eco._a_le_role(membre, s.get("role_id"), s.get("role_nom"))
        if a_le_statut:
            maxi = max(maxi, s.get("ventes_par_semaine") or 0)
    return maxi


async def deja_vendu(eco, joueur_id: str) -> int:
    depuis = _pb_date(debut_semaine())
    lignes = await eco.pb.lister("rec_mouvements", f'joueur="{echapper(joueur_id)}" && sens="reprise" && date>="{depuis}"')
    return sum(l.get("quantite") or 0 for l in lignes)


async def _ligne(eco, objet_id: str) -> dict | None:
    return await eco.pb.premier("rec_objets", f'objet="{echapper(objet_id)}" && actif=true')


async def _mouvement(eco, joueur_id: str, objet_id: str, q: int, prix: int, sens: str, origine: str):
    await eco.pb.creer("rec_mouvements", {"joueur": joueur_id, "objet": objet_id, "quantite": q, "prix_unitaire": prix,
                                          "sens": sens, "date": _pb_date(datetime.now(timezone.utc)), "origine": origine})


async def ajouter_stock(eco, objet_id: str, q: int):
    """Ajoute des unités au stock du Receleur (crée sa ligne, sans prix, si l'objet n'y figure pas encore)."""
    async with _verrou_stock:
        ligne = await eco.pb.premier("rec_objets", f'objet="{echapper(objet_id)}"')
        if ligne:
            await eco.pb.maj("rec_objets", ligne["id"], {"stock": (ligne.get("stock") or 0) + q})
        else:
            await eco.pb.creer("rec_objets", {"objet": objet_id, "prix_reprise": 0, "prix_vente": 0, "stock": q, "actif": True})


async def vendre(eco, membre: discord.Member, objet_id: str, q: int, origine: str) -> tuple[bool, str]:
    """Le joueur vend au Receleur."""
    if q <= 0:
        return False, "La quantité doit être positive."
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return False, "Aucune fiche joueur liée à ton compte Discord."
    r = await reglages(eco)
    if (msg := _ferme(r)):
        return False, msg
    objet = await objet_par_nom(eco, objet_id)
    if not objet:
        return False, "Objet inconnu."
    ligne = await _ligne(eco, objet["id"])
    prix = (ligne or {}).get("prix_reprise") or 0
    if ligne and ligne.get("non_revendable"):
        return False, r.get("message_non_repris") or f"**{objet['nom']}** ne peut pas être revendu au Receleur."
    if not ligne or prix <= 0:
        return False, r.get("message_non_repris") or f"Le Receleur ne reprend pas **{objet['nom']}** pour le moment."
    cfg = await eco.config()
    async with eco._verrou(membre.id), _verrou_stock:
        maxi, fait = await quota(eco, membre, joueur, r), await deja_vendu(eco, joueur["id"])
        reste = max(0, maxi - fait)
        if q > reste:
            return False, (f"Tu as déjà vendu {fait} objet(s) cette semaine sur {maxi} (du lundi au dimanche)."
                           if reste == 0 else f"Il ne te reste que {reste} vente(s) cette semaine (quota {maxi}, du lundi au dimanche).")
        _, possede = await quantite(eco, joueur["id"], objet["id"])
        if possede < q:
            return False, f"Tu n'as que {possede} × {objet['nom']}."
        await retirer_objet(eco, joueur["id"], objet["id"], q, "vente_receleur", f"{prix} Or l'unité")
        j = await changer_or(eco, joueur["id"], prix * q)
        await eco.pb.maj("rec_objets", ligne["id"], {"stock": (ligne.get("stock") or 0) + q})
        await _mouvement(eco, joueur["id"], objet["id"], q, prix, "reprise", origine)
    await _gain(eco, joueur["id"], f"receleur : vente de {objet['nom']}", "Or", prix * q, origine)
    return True, (f"Vendu au Receleur : **{q} × {objet['nom']}** pour **{_n(prix * q)}** {eco_vues.or_txt(cfg)}. "
                  f"Il te reste **{_n(j.get('eco_or'))}** {eco_vues.or_txt(cfg)} ; ventes de la semaine : {fait + q}/{maxi}.")


async def acheter(eco, membre: discord.Member, objet_id: str, q: int, origine: str) -> tuple[bool, str]:
    """Le joueur achète au Receleur (stock du Receleur, aucune limite par joueur)."""
    if q <= 0:
        return False, "La quantité doit être positive."
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return False, "Aucune fiche joueur liée à ton compte Discord."
    r = await reglages(eco)
    if (msg := _ferme(r)):
        return False, msg
    objet = await objet_par_nom(eco, objet_id)
    if not objet:
        return False, "Objet inconnu."
    cfg = await eco.config()
    async with eco._verrou(membre.id), _verrou_stock:
        ligne = await _ligne(eco, objet["id"])
        prix = (ligne or {}).get("prix_vente") or 0
        if not ligne or prix <= 0:
            return False, f"Le Receleur ne vend pas **{objet['nom']}** pour le moment."
        stock = ligne.get("stock") or 0
        if stock < q:
            return False, f"Le Receleur n'a que {stock} × {objet['nom']} en stock." if stock else f"**{objet['nom']}** est épuisé chez le Receleur."
        frais = await eco.pb.requete("GET", f'/api/collections/joueurs/records/{joueur["id"]}')
        total = prix * q
        if (frais.get("eco_or") or 0) < total:
            return False, f"Il te manque {_n(total - (frais.get('eco_or') or 0))} {eco_vues.or_txt(cfg)}."
        j = await changer_or(eco, joueur["id"], -total)
        await eco.pb.maj("joueurs", joueur["id"], {"eco_or_depense": (j.get("eco_or_depense") or 0) + total})
        await eco.ajouter_objet(joueur["id"], objet["id"], q, "achat_receleur", f"{prix} Or l'unité")
        await eco.pb.maj("rec_objets", ligne["id"], {"stock": stock - q})
        await _mouvement(eco, joueur["id"], objet["id"], q, prix, "vente", origine)
    await _gain(eco, joueur["id"], f"receleur : achat de {objet['nom']}", "Or", -total, origine)
    return True, (f"Acheté au Receleur : **{q} × {objet['nom']}** pour **{_n(total)}** {eco_vues.or_txt(cfg)}. "
                  f"Il te reste **{_n(j.get('eco_or'))}** {eco_vues.or_txt(cfg)}.")


async def vue(eco, membre: discord.Member) -> dict:
    """Embed du Receleur : reprise, vente et stock de chaque objet, plus le quota de la semaine du joueur."""
    r = await reglages(eco)
    if (msg := _ferme(r)):
        return eco_vues.erreur(msg)
    cfg = await eco.config()
    objets = {o["id"]: o for o in await eco.pb.lister("eco_objets")}
    lignes = [l for l in await eco.pb.lister("rec_objets", "actif=true") if objets.get(l.get("objet")) and (l.get("prix_reprise") or l.get("prix_vente"))]
    lignes.sort(key=lambda l: objets[l["objet"]]["nom"].lower())
    texte = []
    for l in lignes:
        o = objets[l["objet"]]
        achat = f"vend {_n(l['prix_vente'])}" if l.get("prix_vente") else "ne vend pas"
        stock = f"stock {_n(l.get('stock') or 0)}" if l.get("prix_vente") else ""
        reprise = f"reprend {_n(l['prix_reprise'])}" if l.get("prix_reprise") else "ne reprend pas"
        texte.append(f"{o.get('emoji') or ''} **{o['nom']}** : {reprise} · {achat}" + (f" ({stock})" if stock else ""))
    embed = {"title": "Le Receleur", "color": eco_vues.OR_DEFAUT,
             "description": ("\n".join(texte) or "Rien à proposer pour le moment.")[:4000],
             "footer": {"text": f"Prix en {eco_vues.or_txt(cfg)} par objet. « reprend » = ce que tu reçois en vendant, « vend » = ce que tu paies."}}
    joueur = await eco.joueur_de(membre)
    if joueur:
        maxi, fait = await quota(eco, membre, joueur, r), await deja_vendu(eco, joueur["id"])
        embed["fields"] = [{"name": "Tes ventes cette semaine", "value": f"{fait} / {maxi} (du lundi au dimanche)", "inline": True}]
    if r.get("changement_prix_texte"):
        embed.setdefault("fields", []).append({"name": "Prochain changement des prix", "value": r["changement_prix_texte"], "inline": True})
    return embed


async def surveiller_nouveaux_objets(eco):
    """Tout objet créé après « surveille_depuis » reçoit sa ligne au Receleur (revendable dès qu'un prix de reprise est fixé)
    et les personnes de notif_discord_ids sont prévenues en message privé de lui attribuer un prix."""
    await eco.client.wait_until_ready()
    while not eco.client.is_closed():
        try:
            r = await reglages(eco)
            if r.get("id"):
                if not r.get("surveille_depuis"):
                    await eco.pb.maj("rec_reglages", r["id"], {"surveille_depuis": _pb_date(datetime.now(timezone.utc))})
                else:
                    for o in await eco.pb.lister("eco_objets", f'created>"{r["surveille_depuis"]}"'):
                        if await eco.pb.premier("rec_objets", f'objet="{echapper(o["id"])}"'):
                            continue
                        await eco.pb.creer("rec_objets", {"objet": o["id"], "prix_reprise": 0, "prix_vente": 0, "stock": 0, "actif": True})
                        await _prevenir(eco, r, o)
        except Exception:
            log.exception("Erreur dans la surveillance des nouveaux objets")
        await asyncio.sleep(300)


async def _prevenir(eco, r: dict, objet: dict):
    ids = [x.strip() for x in str(r.get("notif_discord_ids") or "").replace(";", ",").split(",") if x.strip().isdigit()]
    texte = (f"Nouvel objet : **{objet['nom']}**. Il est dans la liste du Receleur sans prix de reprise : "
             "pense à lui en attribuer un (page Receleur du site), sinon les joueurs ne pourront pas le lui vendre.")
    for uid in ids:
        try:
            user = eco.client.get_user(int(uid)) or await eco.client.fetch_user(int(uid))
            await user.send(texte)
        except discord.HTTPException:
            log.warning("Impossible de prévenir %s d'un nouvel objet (messages privés fermés ?)", uid)
