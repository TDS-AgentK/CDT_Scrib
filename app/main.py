import logging
import os

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import FileResponse

from app import eco_interactions, fiche_ephemere
from app.database import Database
from app.discord_types import EPHEMERAL_FLAG, InteractionType, ResponseType
from app.handlers import find_subcommand, handle_autocomplete, handle_command, handle_modal_submit, validate_image_upload
from app.uploads import build_filename, save_bytes
from app.verify import verify_signature

load_dotenv()

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # une ligne par requête PocketBase sinon
log = logging.getLogger("cdt_scrib")

DISCORD_PUBLIC_KEY = os.getenv("DISCORD_PUBLIC_KEY")
DISCORD_APPLICATION_ID = os.getenv("DISCORD_APPLICATION_ID")
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/fiches.db")
UPLOADS_DIR = os.getenv("UPLOADS_DIR") or os.path.join(os.path.dirname(DATABASE_PATH) or ".", "uploads")

if not DISCORD_PUBLIC_KEY:
    raise RuntimeError(
        "DISCORD_PUBLIC_KEY manquant. Copie .env.example vers .env et renseigne-le."
    )

app = FastAPI()
db = Database(DATABASE_PATH)

# Économie (connexion Gateway permanente, en plus de l'endpoint /interactions) : démarrée seulement si
# le jeton du bot et l'accès à la base du site sont configurés ; sinon le bot fonctionne comme avant.
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
PB_URL = os.getenv("PB_URL")
gateway = None
pocketbase = None
economie = None


@app.on_event("startup")
async def startup():
    global gateway, pocketbase, economie
    import asyncio

    # Messages de /fiche voir supprimés 24 h après publication (balayage au démarrage puis toutes les 5 min).
    if DISCORD_TOKEN:
        asyncio.create_task(fiche_ephemere.boucle(db, DISCORD_TOKEN))
    if not (DISCORD_TOKEN and PB_URL):
        log.info("Économie désactivée (DISCORD_TOKEN ou PB_URL absent).")
        return
    import discord

    from app.economie import Economie
    from app.pocketbase import PocketBase

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    gateway = discord.Client(intents=intents)
    pocketbase = PocketBase(PB_URL, os.getenv("PB_EMAIL", ""), os.getenv("PB_PASSWORD", ""))
    economie = Economie(pocketbase, gateway, os.getenv("ECO_PREFIX", "??"), os.getenv("ECO_GUILD_ID") or None)
    gateway.event(economie.on_message)
    # Salons et rôles du serveur copiés dans la base, pour les listes déroulantes de la page Économie du site.
    from app import discord_listes
    synchro_listes = discord_listes.brancher(gateway, pocketbase, economie.guild_id)

    @gateway.event
    async def on_ready():
        log.info("Économie connectée à Discord en tant que %s", gateway.user)
        synchro_listes()
        # /session et /attaque (fiches de jeu) : déclarées ici, sans toucher aux autres commandes ni relancer
        # scripts/register_commands.py à la main.
        from scripts.register_commands import COMMANDS
        for nom in ("session", "attaque"):
            try:
                commande = next(c for c in COMMANDS if c["name"] == nom)
                if os.getenv("GUILD_ID"):
                    await gateway.http.upsert_guild_command(DISCORD_APPLICATION_ID, os.getenv("GUILD_ID"), commande)
                else:
                    await gateway.http.upsert_global_command(DISCORD_APPLICATION_ID, commande)
            except Exception:
                log.exception("Impossible de déclarer la commande /%s", nom)

    asyncio.create_task(gateway.start(DISCORD_TOKEN))
    asyncio.create_task(economie.boucle_roles_temporaires())
    # Embeds de palier Rostheim envoyés à la demande depuis le site (bouton « Envoyer dans le salon »).
    asyncio.create_task(economie.rostheim.boucle_envois())
    # Annonce quotidienne des anniversaires et décès des personnages (réglages : Économie › Anniversaires).
    from app import anniversaires
    asyncio.create_task(anniversaires.boucle(gateway, pocketbase))
    # Drops d'objets non ramassés (rattrapage après redémarrage) et loteries admin (publication, tirage).
    from app import drop, drops_auto, loteries
    asyncio.create_task(drop.boucle(economie))
    asyncio.create_task(loteries.boucle(economie))
    # Drops automatiques programmés sur le site (Loteries & drops › Drops automatiques).
    asyncio.create_task(drops_auto.boucle(economie))
    # Nouveaux objets : ligne au Receleur et rappel à Kyanite de fixer le prix de reprise.
    from app import receleur
    asyncio.create_task(receleur.surveiller_nouveaux_objets(economie))
    # Jets de dés lancés depuis les fiches de jeu du site : publiés dans le salon choisi (#random ou salon perso).
    from app import jets_site
    asyncio.create_task(jets_site.boucle(gateway, pocketbase))
    # Combats (/attaque et bouton « Attaquer » du site) : défis lancés depuis le site, changements de statut, expirations.
    from app import combat
    # Tout passe par la base partagée (comme Rostheim) : aucune variable à poser.
    asyncio.create_task(combat.boucle(gateway, pocketbase))


@app.on_event("shutdown")
async def shutdown():
    await db.close()
    if gateway:
        await gateway.close()
    if pocketbase:
        await pocketbase.close()


@app.get("/")
async def health():
    return {"status": "ok"}


@app.get("/images/{filename}")
async def get_image(filename: str):
    safe_name = os.path.basename(filename)
    path = os.path.join(UPLOADS_DIR, safe_name)
    if not os.path.isfile(path):
        return Response(status_code=404)
    return FileResponse(path)


async def _send_followup(interaction_token: str, embed: dict) -> dict | None:
    """Renvoie le message publié (JSON Discord), ou None en cas d'échec."""
    url = f"https://discord.com/api/v10/webhooks/{DISCORD_APPLICATION_ID}/{interaction_token}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json={"embeds": [embed]}, timeout=10)
            if resp.status_code >= 400:
                log.error("Échec de l'envoi du message de suivi: %s %s", resp.status_code, resp.text)
                return None
            return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.error("Erreur réseau lors de l'envoi du message de suivi: %s", exc)
        return None


async def _get_original(interaction_token: str) -> dict | None:
    url = f"https://discord.com/api/v10/webhooks/{DISCORD_APPLICATION_ID}/{interaction_token}/messages/@original"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            if resp.status_code >= 400:
                log.error("Message d'origine introuvable: %s %s", resp.status_code, resp.text)
                return None
            return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.error("Erreur réseau lors de la lecture du message d'origine: %s", exc)
        return None


async def _fiche_voir_suite(interaction_token: str, followup_embed: dict | None):
    """/fiche voir : envoie les illustrations, puis programme la suppression des deux messages dans 24 h."""
    await fiche_ephemere.programmer(db, await _get_original(interaction_token))
    if followup_embed:
        await fiche_ephemere.programmer(db, await _send_followup(interaction_token, followup_embed))


async def _edit_original(interaction_token: str, content: str):
    url = f"https://discord.com/api/v10/webhooks/{DISCORD_APPLICATION_ID}/{interaction_token}/messages/@original"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.patch(url, json={"content": content}, timeout=10)
            if resp.status_code >= 400:
                log.error("Échec de la mise à jour du message: %s %s", resp.status_code, resp.text)
    except httpx.HTTPError as exc:
        log.error("Erreur réseau lors de la mise à jour du message: %s", exc)


async def _handle_image_upload(nom_id: str, column: str, attachment: dict, base_url: str, interaction_token: str):
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(attachment["url"], timeout=20)
            resp.raise_for_status()
            content = resp.content

        filename = build_filename(attachment.get("content_type", ""))
        save_bytes(UPLOADS_DIR, filename, content)
        public_url = f"{base_url}/images/{filename}"

        await db.update_section(nom_id, {column: public_url})
        await _edit_original(interaction_token, f"Image enregistrée sur `{nom_id}` : {public_url}")
    except httpx.HTTPError as exc:
        log.error("Erreur réseau lors de l'upload d'image: %s", exc)
        await _edit_original(interaction_token, "Erreur réseau pendant le téléchargement de l'image. Réessaie.")
    except OSError as exc:
        log.error("Erreur disque lors de l'upload d'image: %s", exc)
        await _edit_original(interaction_token, "Erreur serveur pendant l'enregistrement de l'image. Réessaie.")


@app.post("/interactions")
async def interactions(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    signature = request.headers.get("X-Signature-Ed25519", "")
    timestamp = request.headers.get("X-Signature-Timestamp", "")

    if not verify_signature(DISCORD_PUBLIC_KEY, signature, timestamp, body):
        return Response(content="invalid request signature", status_code=401)

    payload = await request.json()
    interaction_type = payload["type"]

    if interaction_type == InteractionType.PING:
        return {"type": ResponseType.PONG}

    member_or_user = payload.get("member", {}).get("user") or payload.get("user")

    # Économie : commandes slash (/boutique, /argent…), autocomplétion, clics sur ses menus/boutons et
    # fenêtres (custom_id « eco:… »).
    donnees = payload.get("data") or {}
    if economie is not None and (
        (interaction_type in (InteractionType.APPLICATION_COMMAND, InteractionType.APPLICATION_COMMAND_AUTOCOMPLETE)
         and donnees.get("name") in eco_interactions.COMMANDES)
        or (interaction_type in (InteractionType.MESSAGE_COMPONENT, InteractionType.MODAL_SUBMIT)
            and donnees.get("custom_id", "").startswith("eco:"))
    ):
        return await eco_interactions.repondre(economie, payload, background_tasks, DISCORD_APPLICATION_ID)

    if interaction_type == InteractionType.APPLICATION_COMMAND:
        data = payload["data"]
        sub_name, _ = find_subcommand(data.get("options", []))

        if sub_name == "image":
            error_response, nom_id, column, attachment = await validate_image_upload(db, data, member_or_user)
            if error_response:
                return error_response
            base_url = f"https://{request.headers.get('host')}"
            background_tasks.add_task(
                _handle_image_upload, nom_id, column, attachment, base_url, payload["token"]
            )
            return {
                "type": ResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE,
                "data": {"flags": EPHEMERAL_FLAG},
            }

        response, followup_embed = await handle_command(db, data, member_or_user)
        if sub_name == "voir" and not response.get("data", {}).get("flags"):
            background_tasks.add_task(_fiche_voir_suite, payload["token"], followup_embed)
        elif followup_embed:
            background_tasks.add_task(_send_followup, payload["token"], followup_embed)
        return response

    if interaction_type == InteractionType.APPLICATION_COMMAND_AUTOCOMPLETE:
        return await handle_autocomplete(db, payload["data"], member_or_user)

    if interaction_type == InteractionType.MODAL_SUBMIT:
        response, followup_embed = await handle_modal_submit(db, payload["data"], member_or_user)
        if followup_embed:
            background_tasks.add_task(_send_followup, payload["token"], followup_embed)
        return response

    log.warning("Type d'interaction non géré: %s", interaction_type)
    return Response(status_code=400)
