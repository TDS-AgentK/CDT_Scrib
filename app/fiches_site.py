"""Fiches de personnage créées sur le site (logo contrat de la page du personnage), gardées dans la base du site.

Deux sortes de fiches cohabitent :
- les fiches du bot (base SQLite, /fiche creer et /fiche modifier) : déjà créées ou créées sur Discord, jamais modifiables
  depuis le site ;
- les fiches du site (collection `fiches_discord`) : une par apparence d'un personnage (la « Nature », puis
  `<nom>-<annotation>` ou `<nom>-2`, `<nom>-3`…), modifiables sur le site et sur Discord (/fiche modifier, image,
  supprimer) par le joueur du personnage ou un admin du site. Règle en cas de conflit : la dernière version enregistrée
  vaut (chaque enregistrement ne touche que ses champs) ; une valeur invalide venue de Discord (couleur illisible…) ou un
  enregistrement qui échoue laisse la version du site intacte. Une modification faite sur Discord met aussi à jour les
  messages encore affichés.
`/fiche voir` lit les deux ; le site consulte `fiches_bot_noms` (copie des identifiants du bot) pour ne pas reprendre un
nom déjà pris sur Discord.

Envois depuis le site (collection `fiches_discord_envois`, même principe que `discord_annonces`) : le site dépose une demande
`statut="en_attente"` avec le salon choisi ; le bot poste la fiche (message 1) et ses illustrations (message 2, facultatif),
note les messages et `expire_le` (24 h après l'envoi). `statut="a_modifier"` : le bot remplace les embeds des messages déjà
postés par ceux de la fiche telle qu'enregistrée, sans changer l'heure d'effacement. `statut="a_supprimer"` ou
`expire_le` passée : les messages sont effacés et la demande passe à `supprime`.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Awaitable, Callable

from app.embeds import build_character_embed, build_illustrations_embeds
from app.pocketbase import PocketBase, echapper

log = logging.getLogger("cdt_scrib.fiches_site")

TOUR_S = 5
DUREE = timedelta(hours=24)
NOMS_TOUS_LES = 60  # tours : copie des identifiants du bot toutes les 5 minutes environ

COL_FICHES = "fiches_discord"
COL_ENVOIS = "fiches_discord_envois"
COL_NOMS = "fiches_bot_noms"

CHAMPS_TEXTE = ["nom_skin", "lien_cdt", "nom", "surnom", "genre", "race", "taille", "poids", "tranche_age",
                "morphologie", "couleur_yeux", "couleur_cheveux", "coiffure", "couleur_pelage", "tenue",
                "armement_equipement", "image_personnage_url"]

# (salon_id, embeds) -> message_id
Poster = Callable[[str, list[dict]], Awaitable[str]]
# (salon_id, message_id, embeds) -> False si le message n'existe plus
Editer = Callable[[str, str, list[dict]], Awaitable[bool]]
# (salon_id, message_id) -> efface (sans erreur s'il n'existe plus)
Effacer = Callable[[str, str], Awaitable[None]]


def _pb_date(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S.000Z")


def _lire_date(brut: str | None) -> datetime | None:
    if not brut:
        return None
    try:
        return datetime.fromisoformat(str(brut).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None


def vers_fiche(r: dict) -> SimpleNamespace:
    """Fiche du site -> objet aux mêmes attributs que la Fiche du bot (embeds.build_character_embed)."""
    valeurs = {c: (str(r.get(c) or "").strip() or None) for c in CHAMPS_TEXTE}
    couleur = r.get("couleur")
    illustrations = [i for i in (r.get("illustrations") or []) if isinstance(i, dict) and i.get("url")]
    return SimpleNamespace(
        name=r.get("nom_id", ""), couleur=int(couleur) if isinstance(couleur, (int, float)) and couleur >= 0 else None,
        titre=None, illustrations=illustrations, **valeurs,
        # Champ « image des équipements » des fenêtres Discord : la première illustration.
        equipement_image_url=illustrations[0]["url"] if illustrations else None,
    )


def messages_de(r: dict) -> tuple[dict, list[dict]]:
    """(embed de la fiche, embeds des illustrations) d'une fiche du site."""
    fiche = vers_fiche(r)
    return build_character_embed(fiche), build_illustrations_embeds(fiche)


class FichesSite:
    """Lecture des fiches du site pour /fiche voir, /fiche liste, l'autocomplétion et /fiche creer."""

    def __init__(self, pb: PocketBase):
        self.pb = pb

    async def get(self, nom_id: str) -> dict | None:
        try:
            return await self.pb.premier(COL_FICHES, f'nom_id="{echapper(nom_id.lower())}"')
        except Exception:
            log.exception("Lecture de la fiche du site %s impossible", nom_id)
            return None

    async def noms(self, prefixe: str = "") -> list[str]:
        filtre = f'nom_id~"{echapper(prefixe.lower())}%"' if prefixe else None
        try:
            return sorted(r["nom_id"] for r in await self.pb.lister(COL_FICHES, filtre) if r.get("nom_id"))
        except Exception:
            log.exception("Liste des fiches du site indisponible")
            return []

    async def _comptes(self, discord_id: int) -> tuple[bool, set[str]]:
        """(admin du site ?, noms des comptes du site) liés à ce membre Discord (users.joueur → joueurs.discord_id)."""
        comptes = await self.pb.lister("users", f'joueur.discord_id="{echapper(discord_id)}"')
        return any(c.get("role") == "super_admin" for c in comptes), {c.get("name") or c.get("email") for c in comptes}

    async def peut_gerer(self, discord_id: int, r: dict) -> bool:
        """Mêmes droits que sur le site : le joueur du personnage (personnages.joueur = nom du compte) ou un admin."""
        try:
            admin, noms = await self._comptes(discord_id)
            if admin:
                return True
            perso = await self.pb.requete("GET", f"/api/collections/personnages/records/{r['personnage']}",
                                          params={"fields": "joueur"})
            return bool(perso.get("joueur")) and perso["joueur"] in noms
        except Exception:
            log.exception("Droits sur la fiche du site %s illisibles", r.get("nom_id"))
            return False

    async def noms_geres(self, discord_id: int, prefixe: str = "") -> list[str]:
        """Identifiants des fiches du site que ce membre peut modifier (autocomplétion)."""
        try:
            admin, noms = await self._comptes(discord_id)
            if not admin and not noms:
                return []
            filtres = [f'nom_id~"{echapper(prefixe.lower())}%"'] if prefixe else []
            if not admin:
                filtres.append("(" + " || ".join(f'personnage.joueur="{echapper(n)}"' for n in noms) + ")")
            return sorted(r["nom_id"] for r in await self.pb.lister(COL_FICHES, " && ".join(filtres) or None))
        except Exception:
            log.exception("Fiches du site modifiables indisponibles")
            return []

    async def _rafraichir_envois(self, r: dict) -> None:
        """Messages encore affichés de cette fiche : le bot les met à jour au prochain tour."""
        for e in await self.pb.lister(COL_ENVOIS, f'fiche="{echapper(r["id"])}" && statut="envoye"'):
            await self.pb.maj(COL_ENVOIS, e["id"], {"statut": "a_modifier"})

    async def enregistrer(self, r: dict, valeurs: dict) -> tuple[dict, list[str]]:
        """Enregistre les champs d'une fenêtre Discord ; renvoie (fiche à jour, champs ignorés car invalides).
        Seuls les champs de la fenêtre sont écrits ; une valeur illisible garde celle du site."""
        maj, ignores = {}, []
        for col, brut in valeurs.items():
            brut = (brut or "").strip()
            if col == "couleur":
                if not brut:
                    maj["couleur"] = -1
                else:
                    hexa = brut.lstrip("#")
                    if len(hexa) == 6 and all(c in "0123456789abcdefABCDEF" for c in hexa):
                        maj["couleur"] = int(hexa, 16)
                    else:
                        ignores.append("couleur")
            elif col == "equipement_image_url":
                maj["illustrations"] = _premiere_illustration(r, brut)
            elif col in CHAMPS_TEXTE:
                maj[col] = brut
        nouvelle = await self.pb.maj(COL_FICHES, r["id"], maj) if maj else r
        await self._rafraichir_envois(nouvelle)
        return nouvelle, ignores

    async def image(self, r: dict, colonne: str, url: str) -> dict:
        """/fiche image : image du personnage, ou première illustration (cible « équipement »)."""
        if colonne == "equipement_image_url":
            return await self.enregistrer(r, {"equipement_image_url": url})
        nouvelle = await self.pb.maj(COL_FICHES, r["id"], {"image_personnage_url": url})
        await self._rafraichir_envois(nouvelle)
        return nouvelle, []

    async def supprimer(self, r: dict) -> None:
        """Supprime une apparence (pas la Nature, comme sur le site) ; ses messages encore affichés sont effacés."""
        if r.get("nature"):
            raise ValueError("nature")
        await marquer_effacement(self.pb, r["id"])
        await self.pb.supprimer(COL_FICHES, r["id"])


def _premiere_illustration(r: dict, url: str) -> list[dict]:
    illustrations = [dict(i) for i in (r.get("illustrations") or []) if isinstance(i, dict) and i.get("url")]
    if not url:
        return illustrations[1:]
    if illustrations:
        illustrations[0]["url"] = url
        return illustrations
    return [{"titre": "", "url": url}]


async def marquer_effacement(pb: PocketBase, fiche_id: str) -> None:
    """Avant de supprimer une fiche : ses messages encore affichés passent en effacement (la demande survit à la fiche)."""
    for e in await pb.lister(COL_ENVOIS, f'fiche="{echapper(fiche_id)}" && (statut="envoye" || statut="a_modifier" || statut="en_attente")'):
        await pb.maj(COL_ENVOIS, e["id"], {"statut": "a_supprimer" if e.get("messages") else "supprime"})


async def _poster_tout(poster: Poster, salon: str, fiche: dict) -> list[dict]:
    principal, illustrations = messages_de(fiche)
    messages = [{"salon": salon, "id": await poster(salon, [principal]), "role": "fiche"}]
    if illustrations:
        messages.append({"salon": salon, "id": await poster(salon, illustrations), "role": "illustrations"})
    return messages


async def _effacer_tout(effacer: Effacer, envoi: dict) -> None:
    for m in envoi.get("messages") or []:
        if m.get("id"):
            await effacer(str(m.get("salon") or envoi.get("salon_id") or ""), str(m["id"]))


async def traiter(envoi: dict, pb: PocketBase, poster: Poster, editer: Editer, effacer: Effacer,
                  maintenant: datetime | None = None) -> dict:
    """Applique une demande du site ; renvoie la mise à jour enregistrée sur la demande."""
    maintenant = maintenant or datetime.now(timezone.utc)
    statut = envoi.get("statut")
    salon = str(envoi.get("salon_id") or "")
    try:
        if statut == "a_supprimer" or (statut in ("envoye", "a_modifier")
                                        and (_lire_date(envoi.get("expire_le")) or maintenant) <= maintenant):
            await _effacer_tout(effacer, envoi)
            maj = {"statut": "supprime", "erreur": ""}
        elif not envoi.get("fiche"):  # fiche supprimée entre-temps
            await _effacer_tout(effacer, envoi)
            maj = {"statut": "supprime", "erreur": ""}
        else:
            fiche = (envoi.get("expand") or {}).get("fiche") or await pb.requete("GET", f"/api/collections/{COL_FICHES}/records/{envoi['fiche']}")
            if statut == "en_attente":
                if maintenant - (_lire_date(envoi.get("created")) or maintenant) > DUREE:
                    maj = {"statut": "erreur", "erreur": "trop ancienne, non postée"}
                else:
                    if not salon.isdigit():
                        raise ValueError("salon non réglé")
                    messages = await _poster_tout(poster, salon, fiche)
                    maj = {"statut": "envoye", "messages": messages, "envoye_le": _pb_date(maintenant),
                           "expire_le": _pb_date(maintenant + DUREE), "erreur": ""}
            else:  # a_modifier
                principal, illustrations = messages_de(fiche)
                messages = list(envoi.get("messages") or [])
                fiche_msg = next((m for m in messages if m.get("role") == "fiche"), None)
                illu_msg = next((m for m in messages if m.get("role") == "illustrations"), None)
                if not fiche_msg or not await editer(fiche_msg["salon"], fiche_msg["id"], [principal]):
                    # Fiche effacée à la main entre-temps : on efface le reste.
                    await _effacer_tout(effacer, envoi)
                    maj = {"statut": "supprime", "erreur": ""}
                else:
                    if illu_msg and illustrations:
                        if not await editer(illu_msg["salon"], illu_msg["id"], illustrations):
                            messages.remove(illu_msg)
                            illu_msg = None
                    elif illu_msg:
                        await effacer(illu_msg["salon"], illu_msg["id"])
                        messages.remove(illu_msg)
                    if illustrations and not illu_msg:
                        messages.append({"salon": fiche_msg["salon"], "id": await poster(fiche_msg["salon"], illustrations),
                                         "role": "illustrations"})
                    maj = {"statut": "envoye", "messages": messages, "erreur": ""}
    except Exception as e:  # noqa: BLE001 — l'erreur est notée sur la demande, visible sur le site
        log.exception("Envoi de fiche %s non traité (%s)", envoi.get("id"), statut)
        maj = {"erreur": f"{type(e).__name__} : {e}"[:300]}
        if statut == "en_attente":
            maj["statut"] = "erreur"
    await pb.maj(COL_ENVOIS, envoi["id"], maj)
    return maj


async def tour(pb: PocketBase, poster: Poster, editer: Editer, effacer: Effacer, maintenant: datetime | None = None) -> int:
    maintenant = maintenant or datetime.now(timezone.utc)
    filtre = (f'statut="en_attente" || statut="a_modifier" || statut="a_supprimer" '
              f'|| (statut="envoye" && expire_le<="{_pb_date(maintenant)}")')
    envois = await pb.lister(COL_ENVOIS, filtre, tri="created", expand="fiche")
    for envoi in envois:
        await traiter(envoi, pb, poster, editer, effacer, maintenant)
    return len(envois)


async def copier_noms(pb: PocketBase, noms_bot: list[str]) -> None:
    """Copie les identifiants des fiches du bot dans la base du site (le site ne peut pas lire la base du bot)."""
    attendus = set(noms_bot)
    existants = {r["nom_id"]: r["id"] for r in await pb.lister(COL_NOMS)}
    for nom in attendus - existants.keys():
        await pb.creer(COL_NOMS, {"nom_id": nom})
    for nom in existants.keys() - attendus:
        await pb.supprimer(COL_NOMS, existants[nom])


def _discord(client):
    import discord

    async def salon_de(salon_id: str):
        return client.get_channel(int(salon_id)) or await client.fetch_channel(int(salon_id))

    async def poster(salon_id: str, embeds: list[dict]) -> str:
        message = await (await salon_de(salon_id)).send(
            embeds=[discord.Embed.from_dict(e) for e in embeds], allowed_mentions=discord.AllowedMentions.none())
        return str(message.id)

    async def editer(salon_id: str, message_id: str, embeds: list[dict]) -> bool:
        try:
            await (await salon_de(salon_id)).get_partial_message(int(message_id)).edit(
                embeds=[discord.Embed.from_dict(e) for e in embeds])
        except discord.NotFound:
            return False
        return True

    async def effacer(salon_id: str, message_id: str) -> None:
        try:
            await (await salon_de(salon_id)).get_partial_message(int(message_id)).delete()
        except discord.NotFound:
            pass  # déjà effacé à la main
    return poster, editer, effacer


async def boucle(client, pb: PocketBase, db) -> None:
    await client.wait_until_ready()
    poster, editer, effacer = _discord(client)
    n = 0
    while not client.is_closed():
        try:
            await tour(pb, poster, editer, effacer)
        except Exception:  # collections absentes tant que scripts/setup-fiches-discord.mjs (site) n'a pas tourné
            log.debug("File des fiches du site indisponible", exc_info=True)
        if n % NOMS_TOUS_LES == 0:
            try:
                await copier_noms(pb, await db.list_names())
            except Exception:
                log.debug("Copie des identifiants de fiches indisponible", exc_info=True)
        n += 1
        await asyncio.sleep(TOUR_S)
