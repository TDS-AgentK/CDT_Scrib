"""Actions entre joueurs (inspirées des commandes d'économie de Draftbot) : payer en Or (ou en monnaie
Rostheim si les échanges sont activés sur le site), donner, vendre, utiliser un objet, proposer un échange.
Chaque fonction renvoie (réussi, texte) ; tout mouvement est tracé (eco_gains pour les monnaies,
eco_mouvements pour les objets).
"""
from datetime import datetime, timezone

import discord

from app.pocketbase import echapper


def _pb_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")


def _n(v) -> str:
    return f"{int(v or 0):,}".replace(",", " ")


async def _gain(eco, joueur_id: str, nom: str, monnaie: str, montant: int, origine: str):
    await eco.pb.creer("eco_gains", {"joueur": joueur_id, "commande": nom, "monnaie": monnaie, "montant": montant,
                                     "points_jauge": 0, "date": _pb_date(), "origine": origine})


async def changer_or(eco, joueur_id: str, delta: int) -> dict:
    """Ajoute (ou retire) de l'Or et tient le record d'Or à jour."""
    j = await eco.pb.requete("GET", f"/api/collections/joueurs/records/{joueur_id}")
    nouveau = (j.get("eco_or") or 0) + delta
    maj = {"eco_or": nouveau}
    if nouveau > (j.get("eco_or_record") or 0):
        maj["eco_or_record"] = nouveau
    return await eco.pb.maj("joueurs", joueur_id, maj)


async def quantite(eco, joueur_id: str, objet_id: str) -> tuple[dict | None, int]:
    ligne = await eco.pb.premier("eco_inventaire", f'joueur="{echapper(joueur_id)}" && objet="{echapper(objet_id)}"')
    return ligne, (ligne.get("quantite") or 0) if ligne else 0


async def retirer_objet(eco, joueur_id: str, objet_id: str, q: int, motif: str, note: str = ""):
    ligne, avant = await quantite(eco, joueur_id, objet_id)
    if avant - q <= 0:
        await eco.pb.supprimer("eco_inventaire", ligne["id"])
    else:
        await eco.pb.maj("eco_inventaire", ligne["id"], {"quantite": avant - q})
    await eco.pb.creer("eco_mouvements", {"joueur": joueur_id, "objet": objet_id, "quantite": -q, "motif": motif, "note": note})


async def _deux_joueurs(eco, de: discord.Member, vers: discord.Member):
    if vers.bot or vers.id == de.id:
        return None, None, "Choisis un autre membre que toi."
    j_de, j_vers = await eco.joueur_de(de), await eco.joueur_de(vers)
    if not j_de:
        return None, None, "Aucune fiche joueur liée à ton compte Discord."
    if not j_vers:
        return None, None, f"{vers.display_name} n'a pas de fiche joueur sur le site."
    return j_de, j_vers, None


async def objet_par_nom(eco, nom_ou_id: str) -> dict | None:
    objets = await eco.pb.lister("eco_objets", "actif=true")
    return next((o for o in objets if o["id"] == nom_ou_id), None) or next((o for o in objets if o["nom"].lower() == (nom_ou_id or "").lower()), None)


# ---------------------------------------------------------------- monnaies

MONNAIES_ROSTHEIM = "monnaie"  # préfixe des choix de monnaie Rostheim : « monnaie:<id domaine> »


async def payer(eco, de: discord.Member, vers: discord.Member, montant: int, monnaie: str, origine: str) -> tuple[bool, str]:
    if montant <= 0:
        return False, "Le montant doit être positif."
    j_de, j_vers, err = await _deux_joueurs(eco, de, vers)
    if err:
        return False, err
    cfg = await eco.config()
    from app import eco_vues
    if monnaie == "or":
        async with eco._verrou(de.id):
            frais = await eco.pb.requete("GET", f'/api/collections/joueurs/records/{j_de["id"]}')
            if (frais.get("eco_or") or 0) < montant:
                return False, f"Il te manque {_n(montant - (frais.get('eco_or') or 0))} {eco_vues.or_txt(cfg)}."
            await changer_or(eco, j_de["id"], -montant)
        async with eco._verrou(vers.id):
            await changer_or(eco, j_vers["id"], montant)
        await _gain(eco, j_de["id"], "payer", "Or", -montant, origine)
        await _gain(eco, j_vers["id"], "payer", "Or", montant, origine)
        return True, f"{de.mention} a donné **{_n(montant)}** {eco_vues.or_txt(cfg)} à {vers.mention}."
    # Monnaie Rostheim : seulement si les échanges sont activés dans la page Économie.
    if not cfg["reglages"].get("echanges_actifs"):
        return False, "Les échanges de monnaies Rostheim entre joueurs sont désactivés."
    domaine = next((d for d in await eco.pb.lister("ros_domaines") if d["id"] == monnaie.split(":", 1)[-1]), None)
    if not domaine:
        return False, "Monnaie inconnue."
    maxi = cfg["reglages"].get("echange_max_jour") or 0
    if maxi:
        debut = datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00:00.000Z")
        deja = await eco.pb.lister("eco_gains", f'joueur="{echapper(j_de["id"])}" && commande="payer" && montant<0 && monnaie!="Or" && created>="{debut}"')
        if len(deja) >= maxi:
            return False, f"Limite atteinte : {maxi} échange(s) de monnaie par jour."
    rostheim = eco.rostheim
    async with eco._verrou(de.id):
        s_de = await rostheim._solde(j_de, domaine)
        if (s_de.get("monnaie") or 0) < montant:
            return False, f"Il te manque {_n(montant - (s_de.get('monnaie') or 0))} {domaine.get('monnaie_nom')}."
        await eco.pb.maj("ros_soldes", s_de["id"], {"monnaie": s_de["monnaie"] - montant})
    async with eco._verrou(vers.id):
        s_vers = await rostheim._solde(j_vers, domaine)
        await eco.pb.maj("ros_soldes", s_vers["id"], {"monnaie": (s_vers.get("monnaie") or 0) + montant})
    await _gain(eco, j_de["id"], "payer", domaine.get("monnaie_nom") or "", -montant, origine)
    await _gain(eco, j_vers["id"], "payer", domaine.get("monnaie_nom") or "", montant, origine)
    return True, f"{de.mention} a donné **{_n(montant)}** {domaine.get('monnaie_emoji') or ''} {domaine.get('monnaie_nom')} à {vers.mention}."


# ---------------------------------------------------------------- objets

async def donner(eco, de: discord.Member, vers: discord.Member, objet_id: str, q: int) -> tuple[bool, str]:
    if q <= 0:
        return False, "La quantité doit être positive."
    j_de, j_vers, err = await _deux_joueurs(eco, de, vers)
    if err:
        return False, err
    objet = await objet_par_nom(eco, objet_id)
    if not objet:
        return False, "Objet inconnu."
    async with eco._verrou(de.id):
        _, possede = await quantite(eco, j_de["id"], objet["id"])
        if possede < q:
            return False, f"Tu n'as que {possede} × {objet['nom']}."
        await retirer_objet(eco, j_de["id"], objet["id"], q, "don", f"à {j_vers.get('pseudo')}")
    async with eco._verrou(vers.id):
        await eco.ajouter_objet(j_vers["id"], objet["id"], q, "don", f"de {j_de.get('pseudo')}")
    return True, f"{de.mention} a donné **{q} × {objet.get('emoji') or ''} {objet['nom']}** à {vers.mention}.".replace("  ", " ")


async def vendre(eco, membre: discord.Member, objet_id: str, q: int, origine: str) -> tuple[bool, str]:
    if q <= 0:
        return False, "La quantité doit être positive."
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return False, "Aucune fiche joueur liée à ton compte Discord."
    objet = await objet_par_nom(eco, objet_id)
    if not objet:
        return False, "Objet inconnu."
    prix = objet.get("prix_revente") or 0
    if prix <= 0:
        return False, f"**{objet['nom']}** ne peut pas être revendu."
    cfg = await eco.config()
    from app import eco_vues
    async with eco._verrou(membre.id):
        _, possede = await quantite(eco, joueur["id"], objet["id"])
        if possede < q:
            return False, f"Tu n'as que {possede} × {objet['nom']}."
        await retirer_objet(eco, joueur["id"], objet["id"], q, "vente", f"{prix} Or l'unité")
        j = await changer_or(eco, joueur["id"], prix * q)
    await _gain(eco, joueur["id"], f"vendre : {objet['nom']}", "Or", prix * q, origine)
    return True, f"Vendu : **{q} × {objet['nom']}** pour **{_n(prix * q)}** {eco_vues.or_txt(cfg)}. Il te reste **{_n(j.get('eco_or'))}** {eco_vues.or_txt(cfg)}."


async def utiliser(eco, membre: discord.Member, objet_id: str) -> tuple[bool, str]:
    joueur = await eco.joueur_de(membre)
    if not joueur:
        return False, "Aucune fiche joueur liée à ton compte Discord."
    objet = await objet_par_nom(eco, objet_id)
    if not objet:
        return False, "Objet inconnu."
    if not objet.get("utilisable"):
        return False, f"**{objet['nom']}** ne peut pas être utilisé."
    async with eco._verrou(membre.id):
        _, possede = await quantite(eco, joueur["id"], objet["id"])
        if possede < 1:
            return False, f"Tu n'as pas de {objet['nom']}."
        await retirer_objet(eco, joueur["id"], objet["id"], 1, "utilisation")
    nom = f"{objet.get('emoji') or ''} {objet['nom']}".strip()
    return True, f"{membre.mention} utilise **{nom}**."


# ---------------------------------------------------------------- échanges (proposition puis acceptation)

async def proposer_echange(eco, de: discord.Member, vers: discord.Member, objet_donne: str | None, q_donne: int, or_donne: int,
                           objet_recu: str | None, q_recu: int, or_recu: int, origine: str) -> tuple[bool, str, dict | None]:
    j_de, j_vers, err = await _deux_joueurs(eco, de, vers)
    if err:
        return False, err, None
    od = await objet_par_nom(eco, objet_donne) if objet_donne else None
    orc = await objet_par_nom(eco, objet_recu) if objet_recu else None
    if (objet_donne and not od) or (objet_recu and not orc):
        return False, "Objet inconnu.", None
    if not (od or or_donne > 0) or not (orc or or_recu > 0):
        return False, "Un échange doit avoir quelque chose de chaque côté (objet et/ou Or).", None
    if od:
        _, possede = await quantite(eco, j_de["id"], od["id"])
        if possede < max(1, q_donne):
            return False, f"Tu n'as que {possede} × {od['nom']}.", None
    if or_donne > (j_de.get("eco_or") or 0):
        return False, "Tu n'as pas assez d'Or pour cette offre.", None
    e = await eco.pb.creer("eco_echanges", {
        "de": j_de["id"], "vers": j_vers["id"], "objet_donne": od["id"] if od else "", "quantite_donnee": max(1, q_donne) if od else 0,
        "or_donne": or_donne, "objet_recu": orc["id"] if orc else "", "quantite_recue": max(1, q_recu) if orc else 0, "or_recu": or_recu,
        "statut": "propose", "origine": origine,
    })
    return True, "", e


def _offre_txt(cfg, objet: dict | None, q: int, or_: int) -> str:
    from app import eco_vues
    morceaux = []
    if objet:
        morceaux.append(f"**{q} × {objet.get('emoji') or ''} {objet['nom']}**".replace("  ", " "))
    if or_:
        morceaux.append(f"**{_n(or_)}** {eco_vues.or_txt(cfg)}")
    return " + ".join(morceaux) or "rien"


async def decrire_echange(eco, e: dict) -> tuple[str, str]:
    cfg = await eco.config()
    objets = {o["id"]: o for o in await eco.pb.lister("eco_objets")}
    return (_offre_txt(cfg, objets.get(e.get("objet_donne")), e.get("quantite_donnee") or 0, e.get("or_donne") or 0),
            _offre_txt(cfg, objets.get(e.get("objet_recu")), e.get("quantite_recue") or 0, e.get("or_recu") or 0))


async def repondre_echange(eco, membre: discord.Member, echange_id: str, accepte: bool) -> tuple[bool, str]:
    e = await eco.pb.requete("GET", f"/api/collections/eco_echanges/records/{echange_id}")
    if e.get("statut") != "propose":
        return False, "Cet échange n'est plus en attente."
    joueur = await eco.joueur_de(membre)
    if not joueur or joueur["id"] not in (e["vers"], e["de"]):
        return False, "Cet échange ne te concerne pas."
    if joueur["id"] == e["de"]:
        if accepte:
            return False, "C'est à l'autre joueur d'accepter."
        await eco.pb.maj("eco_echanges", e["id"], {"statut": "annule"})
        return True, "Échange annulé."
    if not accepte:
        await eco.pb.maj("eco_echanges", e["id"], {"statut": "refuse"})
        return True, "Échange refusé."
    # Vérification des deux côtés au moment de l'acceptation, puis transfert.
    j_de = await eco.pb.requete("GET", f'/api/collections/joueurs/records/{e["de"]}')
    j_vers = await eco.pb.requete("GET", f'/api/collections/joueurs/records/{e["vers"]}')
    for j, obj, q, or_ in ((j_de, e.get("objet_donne"), e.get("quantite_donnee"), e.get("or_donne")),
                           (j_vers, e.get("objet_recu"), e.get("quantite_recue"), e.get("or_recu"))):
        if obj:
            _, possede = await quantite(eco, j["id"], obj)
            if possede < q:
                return False, f"{j.get('pseudo')} n'a plus ce qu'il faut pour cet échange."
        if or_ and (j.get("eco_or") or 0) < or_:
            return False, f"{j.get('pseudo')} n'a plus assez d'Or pour cet échange."
    note = f"échange {j_de.get('pseudo')} ↔ {j_vers.get('pseudo')}"
    if e.get("objet_donne"):
        await retirer_objet(eco, j_de["id"], e["objet_donne"], e["quantite_donnee"], "echange", note)
        await eco.ajouter_objet(j_vers["id"], e["objet_donne"], e["quantite_donnee"], "echange", note)
    if e.get("objet_recu"):
        await retirer_objet(eco, j_vers["id"], e["objet_recu"], e["quantite_recue"], "echange", note)
        await eco.ajouter_objet(j_de["id"], e["objet_recu"], e["quantite_recue"], "echange", note)
    for depuis, vers, or_ in ((j_de, j_vers, e.get("or_donne") or 0), (j_vers, j_de, e.get("or_recu") or 0)):
        if or_:
            await changer_or(eco, depuis["id"], -or_)
            await changer_or(eco, vers["id"], or_)
            await _gain(eco, depuis["id"], "échange", "Or", -or_, e.get("origine") or "")
            await _gain(eco, vers["id"], "échange", "Or", or_, e.get("origine") or "")
    await eco.pb.maj("eco_echanges", e["id"], {"statut": "accepte"})
    return True, "Échange effectué ✅"
