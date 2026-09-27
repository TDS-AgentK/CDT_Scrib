import discord

from bot.database import Fiche


def build_embed(fiche: Fiche) -> discord.Embed:
    embed = discord.Embed(
        title=fiche.title,
        description=fiche.description,
        color=fiche.color,
    )
    if fiche.image_url:
        embed.set_image(url=fiche.image_url)
    if fiche.footer:
        embed.set_footer(text=fiche.footer)
    return embed
