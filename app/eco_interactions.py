"""Commandes slash de l'économie, clics sur ses menus/boutons (custom_id « eco:… »), fenêtres (modals) et
autocomplétion, reçus par l'endpoint /interactions.

Discord exige une réponse en moins de 3 s : les écrans rapides (boutique, pages, tri) répondent directement ;
les autres répondent « en cours » puis remplacent le message via le webhook de l'interaction.
"""
import json
import logging

import discord
import httpx

from app import drop, eco_actions, eco_vues, loteries, receleur

log = logging.getLogger("cdt_scrib.eco_interactions")

COMMANDES = {"boutique", "argent", "niveau", "topargent", "topniveau", "inventaire",
             "payer", "donner", "vendre", "utiliser", "echanger", "receleur", "racheter", "drop", "dropadmin", "loterie"}
MESSAGE, DIFFERE, DIFFERE_MAJ, MAJ, AUTOCOMPLETE, FENETRE = 4, 5, 6, 7, 8, 9
PRIVE = eco_vues.PRIVE


# ---------------------------------------------------------------- envoi

async def _webhook(methode: str, url: str, corps: dict, fichier: bytes | None = None):
    try:
        async with httpx.AsyncClient() as client:
            if fichier:
                corps["attachments"] = [{"id": 0, "filename": "carte.png"}]
                r = await client.request(methode, url, data={"payload_json": json.dumps(corps)},
                                         files={"files[0]": ("carte.png", fichier, "image/png")}, timeout=20)
            else:
                r = await client.request(methode, url, json=corps, timeout=10)
            if r.status_code >= 400:
                log.error("Échec de l'envoi Discord : %s %s", r.status_code, r.text)
    except httpx.HTTPError as exc:
        log.error("Erreur réseau vers Discord : %s", exc)


async def _modifier(app_id: str, jeton: str, embeds: list[dict], composants: list | None = None, fichier: bytes | None = None, contenu: str = ""):
    corps = {"content": contenu, "embeds": embeds, "components": composants or [], "allowed_mentions": {"parse": ["users"]}}
    await _webhook("PATCH", f"https://discord.com/api/v10/webhooks/{app_id}/{jeton}/messages/@original", corps, fichier)


async def _suivi_prive(app_id: str, jeton: str, embed: dict):
    await _webhook("POST", f"https://discord.com/api/v10/webhooks/{app_id}/{jeton}", {"embeds": [embed], "flags": PRIVE})


async def _membre(eco, payload: dict, user_id=None) -> discord.Member | None:
    guild = eco.client.get_guild(int(payload["guild_id"])) if payload.get("guild_id") else None
    if not guild:
        return None
    uid = int(user_id or payload["member"]["user"]["id"])
    m = guild.get_member(uid)
    if m:
        return m
    try:
        return await guild.fetch_member(uid)
    except discord.HTTPException:
        return None


def _options(data: dict) -> dict:
    return {o["name"]: o.get("value") for o in data.get("options", [])}


def _maj_v2(vue: dict) -> dict:
    """Mise à jour d'un message Components V2 (le drapeau « privé » ne se change pas après coup)."""
    return {"type": MAJ, "data": {**vue, "flags": eco_vues.V2}}


def _origine(payload: dict) -> str:
    return f'https://discord.com/channels/{payload.get("guild_id")}/{payload.get("channel_id")}' if payload.get("channel_id") else ""


# ---------------------------------------------------------------- point d'entrée

async def repondre(eco, payload: dict, taches, app_id: str) -> dict:
    """Réponse immédiate à l'interaction ; le travail long est confié à `taches` (BackgroundTasks)."""
    try:
        t = payload["type"]
        if t == 4:
            return await _autocompletion(eco, payload)
        membre = await _membre(eco, payload)
        if not membre:
            return {"type": MESSAGE, "data": {"content": "À utiliser sur le serveur.", "flags": PRIVE}}
        if t == 2:
            return await _commande(eco, payload, membre, taches, app_id)
        if t == 3:
            return await _composant(eco, payload, membre, taches, app_id)
        if t == 5:
            return await _fenetre(eco, payload, membre, taches, app_id)
    except Exception:
        log.exception("Erreur pendant une interaction de l'économie")
    return {"type": MESSAGE, "data": {"embeds": [eco_vues.erreur("Une erreur est survenue, réessayez dans un instant.")], "flags": PRIVE}}


def _en_fond(taches, coro_fn, *args):
    async def executer():
        try:
            await coro_fn(*args)
        except Exception:
            log.exception("Erreur en arrière-plan (économie)")
    taches.add_task(executer)


# ---------------------------------------------------------------- commandes slash

async def _commande(eco, payload, membre, taches, app_id) -> dict:
    data, jeton = payload["data"], payload["token"]
    nom, opt = data["name"], _options(data)
    if nom == "boutique":
        return {"type": MESSAGE, "data": await eco_vues.vue_choix_boutique(eco, membre)}

    async def cible():
        if opt.get("membre") and str(opt["membre"]) != str(membre.id):
            cfg = await eco.config()
            if nom in ("argent", "niveau", "inventaire") and not cfg["reglages"].get("voir_niveau_autres"):
                return None
            return await _membre(eco, payload, opt["membre"])
        return membre

    async def travail():
        if nom in ("argent", "niveau"):
            c = await cible()
            if not c:
                return await _modifier(app_id, jeton, [eco_vues.erreur("Les informations des autres membres ne sont pas visibles.")])
            embed, image = await (eco_vues.carte_argent if nom == "argent" else eco_vues.carte_niveau)(eco, c)
            return await _modifier(app_id, jeton, [embed], fichier=image)
        if nom in ("topargent", "topniveau"):
            embed, comp = await eco_vues.vue_classement(eco, "argent" if nom == "topargent" else "niveau")
            return await _modifier(app_id, jeton, [embed], comp)
        if nom == "inventaire":
            c = await cible()
            if not c:
                return await _modifier(app_id, jeton, [eco_vues.erreur("L'inventaire des autres membres n'est pas visible.")])
            embed, comp = await eco_vues.vue_inventaire(eco, c)
            return await _modifier(app_id, jeton, [embed], comp)
        if nom == "payer":
            vers = await _membre(eco, payload, opt.get("membre"))
            ok, txt = await eco_actions.payer(eco, membre, vers, int(opt.get("montant") or 0), opt.get("monnaie") or "or", _origine(payload)) if vers else (False, "Membre introuvable.")
            return await _modifier(app_id, jeton, [eco_vues.resultat(ok, txt)])
        if nom == "donner":
            vers = await _membre(eco, payload, opt.get("membre"))
            ok, txt = await eco_actions.donner(eco, membre, vers, opt.get("objet"), int(opt.get("quantite") or 1)) if vers else (False, "Membre introuvable.")
            return await _modifier(app_id, jeton, [eco_vues.resultat(ok, txt)])
        if nom == "vendre":
            ok, txt = await receleur.vendre(eco, membre, opt.get("objet"), int(opt.get("quantite") or 1), _origine(payload))
            return await _modifier(app_id, jeton, [eco_vues.resultat(ok, txt)])
        if nom == "racheter":
            ok, txt = await receleur.acheter(eco, membre, opt.get("objet"), int(opt.get("quantite") or 1), _origine(payload))
            return await _modifier(app_id, jeton, [eco_vues.resultat(ok, txt)])
        if nom == "receleur":
            return await _modifier(app_id, jeton, [await receleur.vue(eco, membre)])
        if nom == "drop":
            ok, txt = await drop.lancer(eco, membre, opt.get("objet"), int(opt.get("quantite") or 1), int(opt.get("duree") or 0), payload.get("channel_id"))
            return await _modifier(app_id, jeton, [eco_vues.resultat(ok, txt)])
        if nom == "dropadmin":
            ok, txt = await drop.lancer_admin(eco, membre, opt.get("objet"), int(opt.get("quantite") or 1), int(opt.get("or") or 0), int(opt.get("duree") or 0), payload.get("channel_id"))
            return await _modifier(app_id, jeton, [eco_vues.resultat(ok, txt)])
        if nom == "loterie":
            sous = data["options"][0]
            ok, txt = await loteries.creer(eco, membre, _options(sous), payload.get("channel_id"))
            return await _modifier(app_id, jeton, [eco_vues.resultat(ok, txt)])
        if nom == "utiliser":
            salon = membre.guild.get_channel(int(payload["channel_id"])) if payload.get("channel_id") else None
            ok, txt = await eco_actions.utiliser(eco, membre, opt.get("objet"), salon)
            return await _modifier(app_id, jeton, [eco_vues.resultat(ok, txt)])
        if nom == "echanger":
            vers = await _membre(eco, payload, opt.get("membre"))
            if not vers:
                return await _modifier(app_id, jeton, [eco_vues.erreur("Membre introuvable.")])
            ok, txt, e = await eco_actions.proposer_echange(
                eco, membre, vers, opt.get("donne_objet"), int(opt.get("donne_quantite") or 1), int(opt.get("donne_or") or 0),
                opt.get("recoit_objet"), int(opt.get("recoit_quantite") or 1), int(opt.get("recoit_or") or 0), _origine(payload),
                opt.get("donne_monnaie"), int(opt.get("donne_montant") or 0), opt.get("recoit_monnaie"), int(opt.get("recoit_montant") or 0))
            if not ok:
                return await _modifier(app_id, jeton, [eco_vues.erreur(txt)])
            embed, comp = await _vue_echange(eco, e, membre, vers)
            return await _modifier(app_id, jeton, [embed], comp, contenu=vers.mention)

    _en_fond(taches, travail)
    return {"type": DIFFERE, "data": {"flags": PRIVE} if nom in ("vendre", "racheter", "receleur", "drop", "dropadmin", "loterie") else {}}


async def _vue_echange(eco, e: dict, de, vers, statut: str | None = None) -> tuple[dict, list]:
    donne, recoit = await eco_actions.decrire_echange(eco, e)
    embed = {"title": "🤝 Proposition d'échange", "color": eco_vues.OR_DEFAUT,
             "description": f"{de.mention} propose à {vers.mention} :\n\n**Donne** : {donne}\n**Contre** : {recoit}"}
    if statut:
        embed["footer"] = {"text": statut}
        embed["color"] = eco_vues.VERT if statut.startswith("Échange effectué") else eco_vues.GRIS
        return embed, []
    return embed, [{"type": 1, "components": [
        eco_vues._bouton("Accepter", f'eco:ech:{e["id"]}:ok', style=3),
        eco_vues._bouton("Refuser", f'eco:ech:{e["id"]}:no', style=4),
    ]}]


# ---------------------------------------------------------------- menus et boutons

async def _composant(eco, payload, membre, taches, app_id) -> dict:
    data, jeton = payload["data"], payload["token"]
    cid, valeurs = data["custom_id"], data.get("values") or []
    morceaux = cid.split(":")
    if cid == "eco:rien":
        return {"type": DIFFERE_MAJ}
    if cid == "eco:fermer":
        return _maj_v2(eco_vues.boutique_fermee())
    if cid == "eco:shop":
        return _maj_v2(await eco_vues.vue_boutique(eco, membre, valeurs[0]))
    if morceaux[1] == "pg":
        return _maj_v2(await eco_vues.vue_boutique(eco, membre, morceaux[2], int(morceaux[3]), morceaux[4]))
    if morceaux[1] == "tri":
        return _maj_v2(await eco_vues.vue_boutique(eco, membre, morceaux[2], 0, valeurs[0]))
    if morceaux[1] == "buy":
        boutique_id, article_id = morceaux[2], morceaux[3]
        cfg = await eco.config()
        article = next((a for a in cfg["articles"] if a["id"] == article_id), None)
        if article and article.get("type") not in ("role_permanent", "role_temporaire"):
            fenetre = await eco_vues.fenetre_quantite(eco, membre, boutique_id, article_id)
            if fenetre:
                return {"type": FENETRE, "data": fenetre}
        _en_fond(taches, _achat, eco, payload, membre, app_id, boutique_id, article_id, 1)
        return {"type": DIFFERE, "data": {"flags": PRIVE}}
    if morceaux[1] == "top":
        async def page_top():
            embed, comp = await eco_vues.vue_classement(eco, morceaux[2], int(morceaux[3]))
            await _modifier(app_id, jeton, [embed], comp)
        _en_fond(taches, page_top)
        return {"type": DIFFERE_MAJ}
    if morceaux[1] == "inv":
        async def page_inv():
            c = await _membre(eco, payload, morceaux[2]) or membre
            embed, comp = await eco_vues.vue_inventaire(eco, c, int(morceaux[3]))
            await _modifier(app_id, jeton, [embed], comp)
        _en_fond(taches, page_inv)
        return {"type": DIFFERE_MAJ}
    if morceaux[1] in ("drop", "lot"):
        async def clic():
            fn = drop.ramasser if morceaux[1] == "drop" else loteries.participer
            ok, txt = await fn(eco, membre, morceaux[2])
            await _suivi_prive(app_id, jeton, eco_vues.resultat(ok, txt) if ok else eco_vues.erreur(txt))
        _en_fond(taches, clic)
        return {"type": DIFFERE_MAJ}
    if morceaux[1] == "ech":
        async def reponse_echange():
            ok, txt = await eco_actions.repondre_echange(eco, membre, morceaux[2], morceaux[3] == "ok")
            if not ok:
                return await _suivi_prive(app_id, jeton, eco_vues.erreur(txt))
            e = await eco.pb.requete("GET", f"/api/collections/eco_echanges/records/{morceaux[2]}")
            de = await _membre_de_joueur(eco, payload, e["de"])
            vers = await _membre_de_joueur(eco, payload, e["vers"])
            embed, comp = await _vue_echange(eco, e, de or membre, vers or membre, txt)
            await _modifier(app_id, jeton, [embed], comp)
        _en_fond(taches, reponse_echange)
        return {"type": DIFFERE_MAJ}
    return {"type": DIFFERE_MAJ}


async def _membre_de_joueur(eco, payload, joueur_id: str):
    j = await eco.pb.requete("GET", f"/api/collections/joueurs/records/{joueur_id}")
    return await _membre(eco, payload, j["discord_id"]) if j.get("discord_id") else None


async def _achat(eco, payload, membre, app_id, boutique_id, article_id, quantite):
    cfg = await eco.config()
    boutique = next((b for b in cfg["boutiques"] if b["id"] == boutique_id), None)
    article = next((a for a in cfg["articles"] if a["id"] == article_id), None)
    if not boutique or not article:
        return await _modifier(app_id, payload["token"], [eco_vues.resultat_achat(False, "Cet article n'est plus disponible.")])
    ok, texte = await eco.acheter(membre, boutique, article, _origine(payload), quantite)
    await _modifier(app_id, payload["token"], [eco_vues.resultat_achat(ok, texte)])


# ---------------------------------------------------------------- fenêtre de quantité

async def _fenetre(eco, payload, membre, taches, app_id) -> dict:
    data = payload["data"]
    morceaux = data["custom_id"].split(":")
    if morceaux[1] != "buyq":
        return {"type": DIFFERE_MAJ}
    valeur = "1"
    for rangee in data.get("components", []):
        for c in rangee.get("components", []):
            if c.get("custom_id") == "quantite":
                valeur = (c.get("value") or "1").strip()
    if not valeur.isdigit() or not 1 <= int(valeur) <= 25:
        return {"type": MESSAGE, "data": {"embeds": [eco_vues.erreur("Quantité invalide (entre 1 et 25).")], "flags": PRIVE}}
    _en_fond(taches, _achat, eco, payload, membre, app_id, morceaux[2], morceaux[3], int(valeur))
    return {"type": DIFFERE, "data": {"flags": PRIVE}}


# ---------------------------------------------------------------- autocomplétion des objets

async def _autocompletion(eco, payload) -> dict:
    data = payload["data"]
    options = data.get("options", [])
    if options and options[0].get("type") == 1:  # sous-commande (/loterie creer) : les options sont dedans
        options = options[0].get("options", [])
    focus = next((o for o in options if o.get("focused")), None)
    if not focus:
        return {"type": AUTOCOMPLETE, "data": {"choices": []}}
    tape = str(focus.get("value") or "").lower()
    choix = []
    if focus["name"] == "objet" and data["name"] == "racheter":
        objets = {o["id"]: o for o in await eco.pb.lister("eco_objets", "actif=true")}
        for l in await eco.pb.lister("rec_objets", "actif=true && stock>0 && prix_vente>0"):
            o = objets.get(l.get("objet"))
            if o and tape in o["nom"].lower():
                choix.append({"name": f'{o["nom"]} — {l["prix_vente"]} Or (stock {l["stock"]})'[:100], "value": o["id"]})
    elif focus["name"] == "objet" and data["name"] in ("loterie", "dropadmin"):
        for o in await eco.pb.lister("eco_objets", "actif=true", tri="ordre"):
            if tape in o["nom"].lower():
                choix.append({"name": o["nom"][:100], "value": o["id"]})
    elif focus["name"] in ("objet", "donne_objet"):
        membre = await _membre(eco, payload)
        joueur = await eco.joueur_de(membre) if membre else None
        if joueur:
            lignes = await eco.pb.lister("eco_inventaire", f'joueur="{joueur["id"]}" && quantite>0', expand="objet")
            reprises = {}
            if data["name"] == "vendre":
                reprises = {l.get("objet"): l.get("prix_reprise") for l in await eco.pb.lister("rec_objets", "actif=true && prix_reprise>0")}
            for l in lignes:
                o = (l.get("expand") or {}).get("objet")
                if o and tape in o["nom"].lower():
                    suffixe = f" — reprise {reprises[o['id']]} Or" if reprises.get(o["id"]) else ""
                    choix.append({"name": f'{o["nom"]} (×{l["quantite"]}){suffixe}'[:100], "value": o["id"]})
    elif focus["name"] == "recoit_objet":
        for o in await eco.pb.lister("eco_objets", "actif=true", tri="ordre"):
            if tape in o["nom"].lower():
                choix.append({"name": o["nom"][:100], "value": o["id"]})
    elif focus["name"] in ("monnaie", "donne_monnaie", "recoit_monnaie"):
        choix = [{"name": "Or", "value": "or"}] if focus["name"] == "monnaie" else []
        cfg = await eco.config()
        if cfg["reglages"].get("echanges_actifs"):
            for d in await eco.pb.lister("ros_domaines", tri="ordre"):
                choix.append({"name": f'{d.get("monnaie_nom")} ({d.get("nom")})'[:100], "value": f'monnaie:{d["id"]}'})
        choix = [c for c in choix if tape in c["name"].lower()]
    return {"type": AUTOCOMPLETE, "data": {"choices": choix[:25]}}
