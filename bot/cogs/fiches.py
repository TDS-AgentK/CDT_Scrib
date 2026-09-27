import re

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.embed_builder import build_embed

NAME_RE = re.compile(r"^[a-z0-9_-]{1,80}$")


class FicheModal(discord.ui.Modal):
    def __init__(self, bot: commands.Bot, name: str, existing=None):
        super().__init__(title=f"Fiche : {name}")
        self.bot = bot
        self.name = name

        self.title_input = discord.ui.TextInput(
            label="Titre",
            default=existing.title if existing else name,
            max_length=256,
        )
        self.description_input = discord.ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            default=existing.description if existing else "",
            max_length=4000,
            required=False,
        )
        self.color_input = discord.ui.TextInput(
            label="Couleur (hex, ex: 5865F2)",
            default=f"{existing.color:06X}" if existing else "5865F2",
            max_length=6,
            required=False,
        )
        self.image_input = discord.ui.TextInput(
            label="URL image (optionnel)",
            default=existing.image_url if existing and existing.image_url else "",
            required=False,
        )
        self.footer_input = discord.ui.TextInput(
            label="Footer (optionnel)",
            default=existing.footer if existing and existing.footer else "",
            max_length=2048,
            required=False,
        )

        for item in (
            self.title_input,
            self.description_input,
            self.color_input,
            self.image_input,
            self.footer_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            color = int(self.color_input.value or "5865F2", 16)
        except ValueError:
            color = 0x5865F2

        db = self.bot.db
        await db.upsert(
            name=self.name,
            title=self.title_input.value,
            description=self.description_input.value,
            color=color,
            image_url=self.image_input.value or None,
            footer=self.footer_input.value or None,
            author_id=interaction.user.id,
        )
        fiche = await db.get(self.name)
        await interaction.response.send_message(
            content=f"Fiche `{self.bot.config.LOOKUP_PREFIX}{self.name}` enregistrée.",
            embed=build_embed(fiche),
            ephemeral=True,
        )


class FicheNameTransformer(app_commands.Transformer):
    async def transform(self, interaction: discord.Interaction, value: str) -> str:
        value = value.strip().lower()
        if not NAME_RE.match(value):
            raise app_commands.AppCommandError(
                "Nom invalide : lettres minuscules, chiffres, `-` et `_` uniquement (1-80 caractères)."
            )
        return value

    async def autocomplete(self, interaction: discord.Interaction, current: str):
        names = await interaction.client.db.list_names()
        current = current.lower()
        matches = [n for n in names if current in n][:25]
        return [app_commands.Choice(name=n, value=n) for n in matches]


class Fiches(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    fiche_group = app_commands.Group(name="fiche", description="Gérer les fiches")

    @fiche_group.command(name="ajouter", description="Créer une nouvelle fiche")
    @app_commands.describe(nom="Identifiant court de la fiche, ex: regle-1")
    async def ajouter(self, interaction: discord.Interaction, nom: app_commands.Transform[str, FicheNameTransformer]):
        existing = await self.bot.db.get(nom)
        if existing:
            await interaction.response.send_message(
                f"Une fiche `{nom}` existe déjà. Utilise `/fiche modifier`.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(FicheModal(self.bot, nom))

    @fiche_group.command(name="modifier", description="Modifier une fiche existante")
    @app_commands.describe(nom="Identifiant de la fiche à modifier")
    @app_commands.autocomplete(nom=FicheNameTransformer.autocomplete)
    async def modifier(self, interaction: discord.Interaction, nom: str):
        nom = nom.strip().lower()
        existing = await self.bot.db.get(nom)
        if not existing:
            await interaction.response.send_message(
                f"Aucune fiche `{nom}` trouvée.", ephemeral=True
            )
            return
        await interaction.response.send_modal(FicheModal(self.bot, nom, existing))

    @fiche_group.command(name="supprimer", description="Supprimer une fiche")
    @app_commands.describe(nom="Identifiant de la fiche à supprimer")
    @app_commands.autocomplete(nom=FicheNameTransformer.autocomplete)
    async def supprimer(self, interaction: discord.Interaction, nom: str):
        nom = nom.strip().lower()
        deleted = await self.bot.db.delete(nom)
        if deleted:
            await interaction.response.send_message(
                f"Fiche `{nom}` supprimée.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"Aucune fiche `{nom}` trouvée.", ephemeral=True
            )

    @fiche_group.command(name="voir", description="Afficher une fiche")
    @app_commands.describe(nom="Identifiant de la fiche à afficher")
    @app_commands.autocomplete(nom=FicheNameTransformer.autocomplete)
    async def voir(self, interaction: discord.Interaction, nom: str):
        nom = nom.strip().lower()
        fiche = await self.bot.db.get(nom)
        if not fiche:
            await interaction.response.send_message(
                f"Aucune fiche `{nom}` trouvée.", ephemeral=True
            )
            return
        await interaction.response.send_message(embed=build_embed(fiche))

    @fiche_group.command(name="liste", description="Lister toutes les fiches disponibles")
    async def liste(self, interaction: discord.Interaction):
        names = await self.bot.db.list_names()
        if not names:
            await interaction.response.send_message("Aucune fiche enregistrée.", ephemeral=True)
            return
        prefix = self.bot.config.LOOKUP_PREFIX
        formatted = ", ".join(f"`{prefix}{n}`" for n in names)
        await interaction.response.send_message(
            f"**{len(names)} fiche(s) :** {formatted}", ephemeral=True
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        prefix = self.bot.config.LOOKUP_PREFIX
        if not message.content.startswith(prefix):
            return
        name = message.content[len(prefix):].strip().lower()
        if not name or not NAME_RE.match(name):
            return
        fiche = await self.bot.db.get(name)
        if fiche:
            await message.channel.send(embed=build_embed(fiche))


async def setup(bot: commands.Bot):
    await bot.add_cog(Fiches(bot))
