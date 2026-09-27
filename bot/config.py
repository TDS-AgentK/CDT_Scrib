import os

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID") or None
LOOKUP_PREFIX = os.getenv("LOOKUP_PREFIX", "cdt-")
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/fiches.db")

if not DISCORD_TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN manquant. Copie .env.example vers .env et renseigne ton token."
    )
