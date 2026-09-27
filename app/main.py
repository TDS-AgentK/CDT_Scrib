import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response

from app.database import Database
from app.discord_types import InteractionType, ResponseType
from app.handlers import handle_autocomplete, handle_command, handle_modal_submit
from app.verify import verify_signature

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cdt_scrib")

DISCORD_PUBLIC_KEY = os.getenv("DISCORD_PUBLIC_KEY")
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


@app.post("/interactions")
async def interactions(request: Request):
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
        return await handle_command(db, payload["data"], member_or_user)

    if interaction_type == InteractionType.APPLICATION_COMMAND_AUTOCOMPLETE:
        return await handle_autocomplete(db, payload["data"])

    if interaction_type == InteractionType.MODAL_SUBMIT:
        return await handle_modal_submit(db, payload["data"], member_or_user)

    log.warning("Type d'interaction non géré: %s", interaction_type)
    return Response(status_code=400)
