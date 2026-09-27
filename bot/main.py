import asyncio
import logging

import discord
from discord.ext import commands

from bot import config
from bot.database import Database

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cdt_scrib")

INTENTS = discord.Intents.default()
INTENTS.message_content = True


class CdtScribBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=INTENTS)
        self.config = config
        self.db = Database(config.DATABASE_PATH)

    async def setup_hook(self):
        await self.db.connect()
        await self.load_extension("bot.cogs.fiches")

        if config.GUILD_ID:
            guild = discord.Object(id=int(config.GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Commandes synchronisées sur le serveur %s", config.GUILD_ID)
        else:
            await self.tree.sync()
            log.info("Commandes synchronisées globalement")

    async def close(self):
        await self.db.close()
        await super().close()

    async def on_ready(self):
        log.info("Connecté en tant que %s (ID: %s)", self.user, self.user.id)


async def main():
    bot = CdtScribBot()
    async with bot:
        await bot.start(config.DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
