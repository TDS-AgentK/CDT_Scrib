from app.database import SECTION_COLUMNS, Database
from app.discord_types import EPHEMERAL_FLAG, SECTIONS, ResponseType, valid_name
from app.embeds import build_character_embed, build_illustrations_embed, build_section_modal
from app.flavors import CREER, MODIFIER, SUPPRIMER, pick

AUTOCOMPLETE_LIMIT = 25


def _message(content: str = None, *, embeds: list[dict] = None, ephemeral: bool = False) -> dict:
    data = {}
    if content is not None:
        data["content"] = content
    if embeds is not None:
        data["embeds"] = embeds
    if ephemeral:
        data["flags"] = EPHEMERAL_FLAG
    return {"type": ResponseType.CHANNEL_MESSAGE_WITH_SOURCE, "data": data}


def _extract_options(options: list[dict]) -> dict:
    return {opt["name"]: opt.get("value") for opt in options or []}


def _find_subcommand(options: list[dict]) -> tuple[str, dict]:
    sub = options[0]
    return sub["name"], _extract_options(sub.get("options", []))


async def handle_command(db: Database, data: dict, member_or_user: dict) -> tuple[dict, dict | None]:
    sub_name, opts = _find_subcommand(data.get("options", []))
    nom_id = (opts.get("nom") or "").strip().lower()
    user_id = int(member_or_user["id"])

    if sub_name == "liste":
        names = await db.list_names()
        if not names:
            return _message("Aucune fiche enregistrée.", ephemeral=True), None
        formatted = ", ".join(f"`{n}`" for n in names)
        return _message(f"**{len(names)} fiche(s) :** {formatted}", ephemeral=True), None

    if not valid_name(nom_id):
        return _message(
            "Identifiant invalide : lettres minuscules, chiffres, `-` et `_` uniquement (1-80 caractères).",
            ephemeral=True,
        ), None

    if sub_name == "creer":
        existing = await db.get(nom_id)
        if existing:
            return _message(f"Une fiche `{nom_id}` existe déjà.", ephemeral=True), None
        await db.create(nom_id, user_id)
        return _message(
            f"{pick(CREER, nom=nom_id)} Complète-la avec `/fiche modifier nom:{nom_id} section:<...>` "
            "(architecture, identite, physique, apparence). Pour l'architecture, pensez au spycolor, aux "
            "liens (PAS DE LIENS DISCORD, PITIE), skin et compagnie. Je n'ai pas encore de features pour "
            "les animains, courage.",
            ephemeral=True,
        ), None

    if sub_name == "modifier":
        fiche = await db.get(nom_id)
        if not fiche:
            return _message(f"Aucune fiche `{nom_id}` trouvée.", ephemeral=True), None
        if fiche.owner_id != user_id:
            return _message("Cette fiche ne vous appartient pas.", ephemeral=True), None
        section = opts.get("section")
        if section not in SECTIONS:
            return _message("Section invalide.", ephemeral=True), None
        return {
            "type": ResponseType.MODAL,
            "data": build_section_modal(f"fiche_edit:{nom_id}:{section}", section, fiche),
        }, None

    if sub_name == "supprimer":
        fiche = await db.get(nom_id)
        if not fiche:
            return _message(f"Aucune fiche `{nom_id}` trouvée.", ephemeral=True), None
        if fiche.owner_id != user_id:
            return _message("Cette fiche ne vous appartient pas.", ephemeral=True), None
        await db.delete(nom_id)
        return _message(pick(SUPPRIMER, nom=nom_id), ephemeral=True), None

    if sub_name == "voir":
        fiche = await db.get(nom_id)
        if not fiche:
            return _message(f"Aucune fiche `{nom_id}` trouvée.", ephemeral=True), None
        return _message(embeds=[build_character_embed(fiche)]), build_illustrations_embed(fiche)

    return _message("Commande inconnue.", ephemeral=True), None


async def handle_autocomplete(db: Database, data: dict, member_or_user: dict) -> dict:
    sub_name, opts = _find_subcommand(data.get("options", []))
    current = (opts.get("nom") or "").strip().lower()

    if sub_name == "voir":
        names = await db.list_names(prefix=current)
    else:
        user_id = int(member_or_user["id"])
        names = await db.list_names_by_owner(user_id, prefix=current)

    choices = [{"name": n, "value": n} for n in names[:AUTOCOMPLETE_LIMIT]]
    return {"type": ResponseType.APPLICATION_COMMAND_AUTOCOMPLETE_RESULT, "data": {"choices": choices}}


def _modal_values(data: dict) -> dict:
    values = {}
    for row in data.get("components", []):
        for field in row.get("components", []):
            values[field["custom_id"]] = field.get("value", "")
    return values


def _parse_hex_color(raw: str) -> int | None:
    value = raw.strip().lstrip("#")
    if len(value) == 6 and all(c in "0123456789abcdefABCDEF" for c in value):
        return int(value, 16)
    return None


async def handle_modal_submit(db: Database, data: dict, member_or_user: dict):
    """Retourne (response, followup_embed_or_None)."""
    custom_id = data["custom_id"]
    _, nom_id, section = custom_id.split(":", 2)
    raw_values = _modal_values(data)

    columns = SECTION_COLUMNS[section]
    values = {}
    for column in columns:
        raw = raw_values.get(column, "")
        if column == "couleur":
            values[column] = _parse_hex_color(raw)
        else:
            values[column] = raw.strip() or None

    await db.update_section(nom_id, values)
    fiche = await db.get(nom_id)

    response = _message(
        content=pick(MODIFIER, nom=nom_id, section=section),
        embeds=[build_character_embed(fiche)],
        ephemeral=True,
    )
    return response, None
