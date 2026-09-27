from app.database import Fiche
from app.discord_types import ComponentType, TextInputStyle


def build_embed(fiche: Fiche) -> dict:
    embed = {
        "title": fiche.title,
        "description": fiche.description,
        "color": fiche.color,
    }
    if fiche.image_url:
        embed["image"] = {"url": fiche.image_url}
    if fiche.footer:
        embed["footer"] = {"text": fiche.footer}
    return embed


def text_input(custom_id: str, label: str, *, style=TextInputStyle.SHORT,
                value: str = "", required: bool = False, max_length: int | None = None) -> dict:
    field = {
        "type": ComponentType.TEXT_INPUT,
        "custom_id": custom_id,
        "label": label,
        "style": style,
        "required": required,
    }
    if value:
        field["value"] = value
    if max_length:
        field["max_length"] = max_length
    return field


def build_fiche_modal(custom_id: str, name: str, existing: Fiche | None) -> dict:
    rows = [
        text_input("title", "Titre", value=existing.title if existing else name,
                    required=True, max_length=256),
        text_input("description", "Description", style=TextInputStyle.PARAGRAPH,
                    value=existing.description if existing else "", max_length=4000),
        text_input("color", "Couleur (hex, ex: 5865F2)",
                    value=f"{existing.color:06X}" if existing else "5865F2", max_length=6),
        text_input("image", "URL image (optionnel)",
                    value=existing.image_url if existing and existing.image_url else ""),
        text_input("footer", "Footer (optionnel)",
                    value=existing.footer if existing and existing.footer else "", max_length=2048),
    ]
    return {
        "custom_id": custom_id,
        "title": f"Fiche : {name}",
        "components": [
            {"type": ComponentType.ACTION_ROW, "components": [row]} for row in rows
        ],
    }
