from app.database import Database
from app.discord_types import EPHEMERAL_FLAG, ResponseType, valid_name
from app.embeds import build_embed, build_fiche_modal

LOOKUP_LIMIT = 25


def _message(content: str = None, *, embed: dict = None, ephemeral: bool = False) -> dict:
    data = {}
    if content is not None:
        data["content"] = content
    if embed is not None:
        data["embeds"] = [embed]
    if ephemeral:
        data["flags"] = EPHEMERAL_FLAG
    return {"type": ResponseType.CHANNEL_MESSAGE_WITH_SOURCE, "data": data}


def _extract_options(options: list[dict]) -> dict:
    return {opt["name"]: opt.get("value") for opt in options or []}


def _find_subcommand(options: list[dict]) -> tuple[str, dict]:
    sub = options[0]
    return sub["name"], _extract_options(sub.get("options", []))


async def handle_command(db: Database, data: dict, member_or_user: dict) -> dict:
    sub_name, opts = _find_subcommand(data.get("options", []))
    nom = (opts.get("nom") or "").strip().lower()
    user_id = int(member_or_user["id"])

    if sub_name == "liste":
        names = await db.list_names()
        if not names:
            return _message("Aucune fiche enregistrée.", ephemeral=True)
        formatted = ", ".join(f"`{n}`" for n in names)
        return _message(f"**{len(names)} fiche(s) :** {formatted}", ephemeral=True)

    if not valid_name(nom):
        return _message(
            "Nom invalide : lettres minuscules, chiffres, `-` et `_` uniquement (1-80 caractères).",
            ephemeral=True,
        )

    if sub_name == "ajouter":
        existing = await db.get(nom)
        if existing:
            return _message(f"Une fiche `{nom}` existe déjà. Utilise `/fiche modifier`.", ephemeral=True)
        return {"type": ResponseType.MODAL, "data": build_fiche_modal(f"fiche_add:{nom}", nom, None)}

    if sub_name == "modifier":
        existing = await db.get(nom)
        if not existing:
            return _message(f"Aucune fiche `{nom}` trouvée.", ephemeral=True)
        return {"type": ResponseType.MODAL, "data": build_fiche_modal(f"fiche_edit:{nom}", nom, existing)}

    if sub_name == "supprimer":
        deleted = await db.delete(nom)
        if deleted:
            return _message(f"Fiche `{nom}` supprimée.", ephemeral=True)
        return _message(f"Aucune fiche `{nom}` trouvée.", ephemeral=True)

    if sub_name == "voir":
        fiche = await db.get(nom)
        if not fiche:
            return _message(f"Aucune fiche `{nom}` trouvée.", ephemeral=True)
        return _message(embed=build_embed(fiche))

    return _message("Commande inconnue.", ephemeral=True)


async def handle_autocomplete(db: Database, data: dict) -> dict:
    sub_name, opts = _find_subcommand(data.get("options", []))
    current = (opts.get("nom") or "").strip().lower()
    names = await db.list_names(prefix=current)
    choices = [{"name": n, "value": n} for n in names[:LOOKUP_LIMIT]]
    return {"type": ResponseType.APPLICATION_COMMAND_AUTOCOMPLETE_RESULT, "data": {"choices": choices}}


def _modal_values(data: dict) -> dict:
    values = {}
    for row in data.get("components", []):
        for field in row.get("components", []):
            values[field["custom_id"]] = field.get("value", "")
    return values


async def handle_modal_submit(db: Database, data: dict, member_or_user: dict) -> dict:
    custom_id = data["custom_id"]
    _, nom = custom_id.split(":", 1)
    values = _modal_values(data)

    try:
        color = int(values.get("color") or "5865F2", 16)
    except ValueError:
        color = 0x5865F2

    await db.upsert(
        name=nom,
        title=values.get("title", nom),
        description=values.get("description", ""),
        color=color,
        image_url=values.get("image") or None,
        footer=values.get("footer") or None,
        author_id=int(member_or_user["id"]),
    )
    fiche = await db.get(nom)
    return _message(
        content=f"Fiche `{nom}` enregistrée.",
        embed=build_embed(fiche),
        ephemeral=True,
    )
