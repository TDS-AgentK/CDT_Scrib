from app.database import Fiche
from app.discord_types import ComponentType, TextInputStyle

SKIN_BASE_URL = "http://chipset.slayersonline.net/Miniature/"

# (colonne, libellé affiché, inline)
CHARACTER_FIELDS = [
    ("genre", "Sexe", True),
    ("race", "Race", True),
    ("taille", "Taille", True),
    ("poids", "Poids", True),
    ("morphologie", "Morphologie", True),
    ("tranche_age", "Tranche d'âge physique", True),
    ("couleur_yeux", "Couleur des yeux", True),
    ("couleur_cheveux", "Couleur et longueur des cheveux", False),
    ("coiffure", "Coiffure", False),
    ("tenue", "Tenue", False),
    ("armement_equipement", "Armement & équipement", False),
]


def _skin_url(nom_skin: str | None) -> str | None:
    return f"{SKIN_BASE_URL}{nom_skin}" if nom_skin else None


def build_character_embed(fiche: Fiche) -> dict:
    embed: dict = {"description": fiche.surnom if fiche.surnom else "..."}

    if fiche.nom:
        embed["title"] = fiche.nom
    if fiche.couleur is not None:
        embed["color"] = fiche.couleur

    fields = [
        {"name": label, "value": getattr(fiche, column), "inline": inline}
        for column, label, inline in CHARACTER_FIELDS
        if getattr(fiche, column)
    ]
    if fields:
        embed["fields"] = fields

    if fiche.image_personnage_url:
        embed["image"] = {"url": fiche.image_personnage_url}
        embed["thumbnail"] = {"url": fiche.image_personnage_url}

    skin_url = _skin_url(fiche.nom_skin)

    if fiche.nom:
        author = {"name": fiche.nom}
        if fiche.lien_cdt:
            author["url"] = fiche.lien_cdt
        if skin_url:
            author["icon_url"] = skin_url
        embed["author"] = author

        footer_text = f"{fiche.nom}, {fiche.surnom}" if fiche.surnom else fiche.nom
        footer = {"text": footer_text}
        if skin_url:
            footer["icon_url"] = skin_url
        embed["footer"] = footer

    return embed


def build_illustrations_embed(fiche: Fiche) -> dict | None:
    if not fiche.equipement_image_url:
        return None
    embed = {"title": "Illustrations", "image": {"url": fiche.equipement_image_url}}
    if fiche.couleur is not None:
        embed["color"] = fiche.couleur
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


SECTION_MODAL_DEFS = {
    "architecture": {
        "title": "Fiche · Architecture",
        "fields": [
            ("couleur", "Couleur (valeur décimale, spycolor.com)", TextInputStyle.SHORT, 10),
            ("image_personnage_url", "Lien de l'image du personnage", TextInputStyle.SHORT, None),
            ("nom_skin", "Nom de la skin (ex: Sei.png)", TextInputStyle.SHORT, 128),
            ("lien_cdt", "Lien de la fiche sur les CDT", TextInputStyle.SHORT, None),
            ("equipement_image_url", "Lien image des équipements (optionnel)", TextInputStyle.SHORT, None),
        ],
    },
    "identite": {
        "title": "Fiche · Identité",
        "fields": [
            ("nom", "Nom du personnage", TextInputStyle.SHORT, 256),
            ("surnom", "Surnom (optionnel)", TextInputStyle.SHORT, 256),
            ("genre", "Genre", TextInputStyle.SHORT, 64),
            ("race", "Race", TextInputStyle.SHORT, 64),
        ],
    },
    "physique": {
        "title": "Fiche · Physique",
        "fields": [
            ("taille", "Taille", TextInputStyle.SHORT, 64),
            ("poids", "Poids", TextInputStyle.SHORT, 64),
            ("tranche_age", "Tranche d'âge physique", TextInputStyle.SHORT, 64),
            ("morphologie", "Morphologie", TextInputStyle.SHORT, 128),
            ("couleur_yeux", "Couleur des yeux", TextInputStyle.SHORT, 64),
        ],
    },
    "apparence": {
        "title": "Fiche · Apparence",
        "fields": [
            ("couleur_cheveux", "Couleur et longueur des cheveux", TextInputStyle.SHORT, 256),
            ("coiffure", "Coiffure", TextInputStyle.PARAGRAPH, 1024),
            ("tenue", "Tenue", TextInputStyle.PARAGRAPH, 1024),
            ("armement_equipement", "Armement & équipement", TextInputStyle.PARAGRAPH, 1024),
        ],
    },
}


def build_section_modal(custom_id: str, section: str, fiche: Fiche | None) -> dict:
    definition = SECTION_MODAL_DEFS[section]
    rows = []
    for column, label, style, max_length in definition["fields"]:
        existing_value = getattr(fiche, column, None) if fiche else None
        rows.append(
            text_input(
                column,
                label,
                style=style,
                value="" if existing_value is None else str(existing_value),
                max_length=max_length,
            )
        )
    return {
        "custom_id": custom_id,
        "title": definition["title"],
        "components": [{"type": ComponentType.ACTION_ROW, "components": [row]} for row in rows],
    }
