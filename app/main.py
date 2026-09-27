import logging
import os

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request, Response

from app.database import Database
from app.discord_types import InteractionType, ResponseType
from app.handlers import handle_autocomplete, handle_command, handle_modal_submit
from app.verify import verify_signature

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cdt_scrib")

DISCORD_PUBLIC_KEY = os.getenv("DISCORD_PUBLIC_KEY")
DISCORD_APPLICATION_ID = os.getenv("DISCORD_APPLICATION_ID")
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/fiches.db")

if not DISCORD_PUBLIC_KEY:
    raise RuntimeError(
        "DISCORD_PUBLIC_KEY manquant. Copie .env.example vers .env et renseigne-le."
    )

app = FastAPI()
db = Database(DATABASE_PATH)


@app.on_event("shutdown")
async def shutdown():
    await db.close()


@app.get("/")
async def health():
    return {"status": "ok"}


async def _send_followup(interaction_token: str, embed: dict):
    url = f"https://discord.com/api/v10/webhooks/{DISCORD_APPLICATION_ID}/{interaction_token}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json={"embeds": [embed]}, timeout=10)
            if resp.status_code >= 400:
                log.error("Échec de l'envoi du message de suivi: %s %s", resp.status_code, resp.text)
    except httpx.HTTPError as exc:
        log.error("Erreur réseau lors de l'envoi du message de suivi: %s", exc)


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

    if interaction_type == InteractionType.APPLICATION_COMMAND:
        response, followup_embed = await handle_command(db, payload["data"], member_or_user)
        if followup_embed:
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
