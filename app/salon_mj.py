"""Salons MJ éphémères (/salon_mj, demande de K du 26/11/2026).

/salon_mj titre:… crée un salon texte tout en haut de la catégorie « Coin RP » ; le joueur qui lance la commande peut y
écrire et y envoyer des images (droits de la catégorie conservés pour les autres).

24 h après le dernier message, le bot :
  1. relit tout le salon et fabrique une archive HTML autonome (messages, auteurs, dates, avatars, images réduites et
     intégrées dans le fichier : les liens des images Discord expirent) dans un dossier temporaire ;
  2. dépose l'archive sur le site (collection PocketBase `salons_mj`, champ fichier), gardée ARCHIVE_JOURS jours ;
  3. poste dans le salon d'archive un lien secret vers le site (/archives-salons/<clé>, lecture ou téléchargement) ;
  4. supprime le salon Discord, puis le dossier temporaire.
Pas de limite des 10 Mo de Discord : le fichier ne passe pas par Discord.

Tout l'état est dans la base du site (une fiche par salon) et l'inactivité se lit sur Discord (identifiant du dernier
message = son heure) : un redémarrage du bot ne perd rien, le balayage reprend au démarrage puis toutes les 5 minutes.
Les archives périmées sont effacées par le même balayage (le fichier part avec la fiche).

Réglages (variables d'environnement, facultatives sauf le salon d'archive) :
  SALON_MJ_ARCHIVES_ID    salon où poster les liens (sans lui, les salons sont archivés sur le site mais aucun lien
                          n'est posté : le salon Discord est alors gardé, pour ne rien perdre)
  SALON_MJ_CATEGORIE_ID   catégorie des salons (sinon celle dont le nom contient « coin rp »)
"""
import asyncio
import base64
import html
import io
import logging
import os
import re
import secrets
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import discord
import httpx

log = logging.getLogger("cdt_scrib.salon_mj")

COLLECTION = "salons_mj"
INACTIVITE_S = 24 * 3600
ARCHIVE_JOURS = 30
INTERVALLE_S = 5 * 60
PARIS = ZoneInfo("Europe/Paris")
SITE_PAR_DEFAUT = "https://cdtsite-production.up.railway.app"
DISCORD_EPOCH_MS = 1420070400000

IMAGE_COTE_MAX = 1600          # côté le plus long des images intégrées
IMAGE_QUALITE = 80             # WebP
# Aucune limite de taille (demande de K du 26/11/2026) : GIF animés gardés tels quels, toutes les autres pièces jointes
# (PDF, texte, son…) intégrées ; seules les images fixes sont réduites.
EXTENSIONS_IMAGE = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


# ---------------------------------------------------------------- outils

def heure_snowflake(snowflake) -> float:
    """Heure (secondes Unix) encodée dans un identifiant Discord."""
    return ((int(snowflake) >> 22) + DISCORD_EPOCH_MS) / 1000


def nom_salon(titre: str) -> str:
    """Nom de salon Discord à partir du titre (minuscules, tirets ; accents gardés)."""
    t = unicodedata.normalize("NFC", titre or "").strip().lower()
    t = re.sub(r"[\s_]+", "-", t)
    t = re.sub(r"[^\w\-]", "", t)
    t = re.sub(r"-{2,}", "-", t).strip("-")
    return (t or "salon-mj")[:100]


def nouvelle_cle() -> str:
    return secrets.token_urlsafe(24)  # 32 caractères, impossible à deviner


def url_site() -> str:
    return (os.getenv("SITE_URL") or SITE_PAR_DEFAUT).rstrip("/")


def lien_archive(cle: str) -> str:
    return f"{url_site()}/archives-salons/{cle}"


def date_fr(dt: datetime) -> str:
    return dt.astimezone(PARIS).strftime("%d/%m/%Y à %H:%M")


def data_uri(contenu: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(contenu).decode('ascii')}"


def reduire_image(contenu: bytes, cote: int = IMAGE_COTE_MAX) -> tuple[bytes, str]:
    """Image réduite (WebP, côté ≤ `cote`) ; l'original est gardé s'il est déjà plus léger.
    Renvoie (octets, type MIME). Lève une exception si ce n'est pas une image lisible."""
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(contenu))
    anime = getattr(img, "is_animated", False)
    if anime:
        return contenu, Image.MIME.get(img.format, "image/gif")
    img.seek(0)
    img = ImageOps.exif_transpose(img) if not anime else img
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() or img.mode == "P" else "RGB")
    img.thumbnail((cote, cote))
    sortie = io.BytesIO()
    img.save(sortie, "WEBP", quality=IMAGE_QUALITE, method=4)
    reduite = sortie.getvalue()
    if len(reduite) < len(contenu) or anime:
        return reduite, "image/webp"
    mime = Image.MIME.get(Image.open(io.BytesIO(contenu)).format) or "application/octet-stream"
    return contenu, mime


# ---------------------------------------------------------------- texte des messages (Markdown Discord simplifié)

_CODE_BLOC = re.compile(r"```(?:[\w+-]*\n)?(.*?)```", re.S)
_CODE = re.compile(r"`([^`\n]+)`")


def rendre_texte(texte: str, noms: dict | None = None) -> str:
    """Texte Discord → HTML sûr : tout est échappé, puis gras, italique, souligné, barré, spoilers, code, citations,
    liens et mentions (<@id>, <#id>, <@&id> remplacés par les noms connus)."""
    noms = noms or {}
    if not texte:
        return ""
    blocs: list[str] = []

    def garder(contenu: str) -> str:
        blocs.append(contenu)
        return f"\x00{len(blocs) - 1}\x00"

    t = _CODE_BLOC.sub(lambda m: garder(f"<pre>{html.escape(m.group(1))}</pre>"), texte)
    t = _CODE.sub(lambda m: garder(f"<code>{html.escape(m.group(1))}</code>"), t)
    t = re.sub(r"<?(https?://[^\s<>]+[^\s<>.,;:!?)\]'\"])>?", lambda m: garder(
        f'<a href="{html.escape(m.group(1))}" rel="noopener noreferrer" target="_blank">{html.escape(m.group(1))}</a>'), t)
    t = html.escape(t, quote=False)

    def mention(m):
        genre, ident = m.group(1), m.group(2)
        cle = {"@": "u", "@!": "u", "#": "c", "@&amp;": "r"}[genre] + ident
        prefixe = "#" if genre == "#" else "@"
        return f'<span class="mention">{prefixe}{html.escape(noms.get(cle, "inconnu"))}</span>'

    t = re.sub(r"&lt;(@!?|#|@&amp;)(\d+)&gt;", mention, t)
    t = re.sub(r"&lt;a?:(\w+):\d+&gt;", r":\1:", t)  # émojis personnalisés
    t = re.sub(r"&lt;t:(\d+)(?::\w)?&gt;", lambda m: date_fr(datetime.fromtimestamp(int(m.group(1)), timezone.utc)), t)
    t = re.sub(r"\|\|(.+?)\|\|", r'<span class="spoiler" title="Spoiler (cliquer)" onclick="this.classList.toggle(\'vu\')">\1</span>', t, flags=re.S)
    t = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", t, flags=re.S)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t, flags=re.S)
    t = re.sub(r"__(.+?)__", r"<u>\1</u>", t, flags=re.S)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", t, flags=re.S)
    t = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"<em>\1</em>", t, flags=re.S)
    t = re.sub(r"~~(.+?)~~", r"<s>\1</s>", t, flags=re.S)
    lignes = []
    for ligne in t.split("\n"):
        if ligne.startswith("&gt; "):
            ligne = f'<span class="citation">{ligne[5:]}</span>'
        elif ligne.startswith("# ") or ligne.startswith("## ") or ligne.startswith("### "):
            niv = len(ligne.split(" ", 1)[0])
            ligne = f'<span class="titre{niv}">{ligne[niv + 1:]}</span>'
        lignes.append(ligne)
    t = "<br>".join(lignes).replace("<br><span class=\"citation\">", "<span class=\"citation\">")
    return re.sub(r"\x00(\d+)\x00", lambda m: blocs[int(m.group(1))], t)


# ---------------------------------------------------------------- page HTML

STYLE = """
:root{color-scheme:dark;--fond:#1f1b16;--carte:#2a241d;--texte:#ece3d3;--doux:#a89c88;--or:#d6b36a;--lien:#8fc1e8}
*{box-sizing:border-box}body{margin:0;background:var(--fond);color:var(--texte);font:15px/1.5 Georgia,"Times New Roman",serif}
header{padding:28px 20px 18px;border-bottom:1px solid #4a3f31;text-align:center}
header h1{margin:0 0 6px;color:var(--or);font-size:1.7em;font-weight:normal;letter-spacing:.02em}
header p{margin:2px 0;color:var(--doux);font-size:.92em}
main{max-width:900px;margin:0 auto;padding:12px 16px 60px}
.jour{margin:26px 0 8px;text-align:center;color:var(--doux);font-size:.85em;letter-spacing:.08em;text-transform:uppercase}
.msg{display:flex;gap:12px;padding:8px 10px;border-radius:6px}.msg:hover{background:#ffffff08}
.msg.suite{padding-top:1px}.msg.suite .avatar{visibility:hidden;height:0}
.avatar{width:40px;height:40px;border-radius:50%;flex:none;background:#3a3127;object-fit:cover}
.corps{min-width:0;flex:1}.tete{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.auteur{font-weight:bold;color:var(--or)}.date{color:var(--doux);font-size:.78em}.modifie{color:var(--doux);font-size:.75em}
.texte{white-space:normal;overflow-wrap:anywhere}.reponse{color:var(--doux);font-size:.82em;margin-bottom:2px}
a{color:var(--lien)}code{background:#00000040;padding:1px 4px;border-radius:3px;font-size:.9em}
pre{background:#00000040;padding:8px;border-radius:4px;white-space:pre-wrap;font-size:.88em}
.citation{display:block;border-left:3px solid #6b5c47;padding-left:8px;margin:2px 0}
.titre1{font-size:1.4em;font-weight:bold}.titre2{font-size:1.2em;font-weight:bold}.titre3{font-size:1.05em;font-weight:bold}
.mention{color:#c9b7ff;background:#5865f226;border-radius:3px;padding:0 2px}
.spoiler{background:#111;color:transparent;border-radius:3px;cursor:pointer}.spoiler.vu{background:#ffffff14;color:inherit}
.images{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}
.images img{max-width:min(100%,520px);max-height:520px;border-radius:6px;cursor:zoom-in;display:block}
.fichier{display:inline-block;margin-top:6px;padding:6px 10px;background:var(--carte);border-radius:5px;font-size:.9em}
.embed{margin-top:6px;border-left:4px solid var(--or);background:var(--carte);padding:8px 12px;border-radius:4px;max-width:560px}
.embed .et{font-weight:bold}.embed img{max-width:100%;border-radius:4px;margin-top:6px}
.systeme{color:var(--doux);font-style:italic;font-size:.9em;padding:4px 62px}
footer{text-align:center;color:var(--doux);font-size:.8em;padding:20px}
#loupe{position:fixed;inset:0;background:#000d;display:none;align-items:center;justify-content:center;cursor:zoom-out}
#loupe img{max-width:96vw;max-height:96vh}
"""

SCRIPT_LOUPE = """document.addEventListener('click',function(e){var l=document.getElementById('loupe');
if(e.target.matches('.images img')){l.firstChild.src=e.target.src;l.style.display='flex'}else if(e.target.closest('#loupe')){l.style.display='none'}});"""


def page_html(info: dict, messages: list[dict]) -> str:
    """Archive autonome. `info` : titre, salon, createur, debut, fin (datetime), genere (datetime).
    `messages` : dicts déjà prêts (auteur, avatar, date, texte_html, images [uri], fichiers [(nom, uri|None, taille)],
    embeds [html], reponse_a, modifie, systeme)."""
    e = html.escape
    lignes = [
        "<!DOCTYPE html>", '<html lang="fr"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">', '<meta name="robots" content="noindex">',
        f"<title>{e(info['titre'])} · archive du salon</title><style>{STYLE}</style></head><body>",
        f"<header><h1>{e(info['titre'])}</h1>",
        f"<p>Salon #{e(info['salon'])} · ouvert par {e(info['createur'])}</p>",
        f"<p>Du {date_fr(info['debut'])} au {date_fr(info['fin'])} · {sum(1 for m in messages if not m.get('systeme'))} message(s)</p>",
        "</header><main>",
    ]
    jour_prec, auteur_prec, heure_prec = None, None, 0.0
    for m in messages:
        jour = m["date"].astimezone(PARIS).strftime("%d/%m/%Y")
        if jour != jour_prec:
            lignes.append(f'<div class="jour">{jour}</div>')
            jour_prec, auteur_prec = jour, None
        if m.get("systeme"):
            lignes.append(f'<div class="systeme">{m["texte_html"]}</div>')
            auteur_prec = None
            continue
        ts = m["date"].timestamp()
        suite = auteur_prec == m["auteur_id"] and ts - heure_prec < 420 and not m.get("reponse_a")
        auteur_prec, heure_prec = m["auteur_id"], ts
        avatar = f'<img class="avatar" src="{m["avatar"]}" alt="">' if m.get("avatar") else '<div class="avatar"></div>'
        bloc = [f'<div class="msg{" suite" if suite else ""}">{avatar}<div class="corps">']
        if m.get("reponse_a"):
            bloc.append(f'<div class="reponse">↪ en réponse à {e(m["reponse_a"])}</div>')
        if not suite:
            bloc.append(f'<div class="tete"><span class="auteur">{e(m["auteur"])}</span>'
                        f'<span class="date">{date_fr(m["date"])}</span></div>')
        if m.get("texte_html"):
            modifie = ' <span class="modifie">(modifié)</span>' if m.get("modifie") else ""
            bloc.append(f'<div class="texte">{m["texte_html"]}{modifie}</div>')
        for emb in m.get("embeds") or []:
            bloc.append(f'<div class="embed">{emb}</div>')
        if m.get("images"):
            bloc.append('<div class="images">' + "".join(f'<img src="{u}" alt="" loading="lazy">' for u in m["images"]) + "</div>")
        for nom, uri, taille in m.get("fichiers") or []:
            poids = f" ({taille / 1024 ** 2:.1f} Mo)" if taille >= 1024 ** 2 else f" ({max(1, taille // 1024)} Ko)"
            if uri:
                bloc.append(f'<a class="fichier" href="{uri}" download="{e(nom)}">📎 {e(nom)}{poids}</a>')
            else:
                bloc.append(f'<span class="fichier">📎 {e(nom)}{poids} — introuvable sur Discord, non conservé</span>')
        bloc.append("</div></div>")
        lignes.append("".join(bloc))
    lignes.append(f"</main><footer>Archive générée le {date_fr(info['genere'])} par le Scribe des Chroniques du Temps.</footer>")
    lignes.append(f'<div id="loupe"><img alt=""></div><script>{SCRIPT_LOUPE}</script></body></html>')
    return "\n".join(lignes)


# ---------------------------------------------------------------- lecture du salon Discord

async def _telecharger(client: httpx.AsyncClient, url: str) -> bytes | None:
    for essai in range(3):
        try:
            r = await client.get(url, timeout=60)
            if r.status_code == 200:
                return r.content
            if r.status_code in (403, 404):
                return None
        except httpx.HTTPError:
            pass
        await asyncio.sleep(1 + essai * 2)
    return None


def _embed_html(emb: discord.Embed, images: dict) -> str:
    e = html.escape
    morceaux = []
    if emb.author and emb.author.name:
        morceaux.append(f"<div>{e(emb.author.name)}</div>")
    if emb.title:
        morceaux.append(f'<div class="et">{e(emb.title)}</div>')
    if emb.description:
        morceaux.append(f"<div>{rendre_texte(emb.description)}</div>")
    for f in emb.fields:
        morceaux.append(f"<div><strong>{e(f.name or '')}</strong><br>{rendre_texte(f.value or '')}</div>")
    url = (emb.image and emb.image.url) or (emb.thumbnail and emb.thumbnail.url)
    if url and images.get(url):
        morceaux.append(f'<img src="{images[url]}" alt="">')
    if emb.footer and emb.footer.text:
        morceaux.append(f'<div class="date">{e(emb.footer.text)}</div>')
    return "".join(morceaux)


async def lire_salon(salon: discord.TextChannel) -> list[dict]:
    """Tous les messages du salon, du plus ancien au plus récent, prêts pour page_html (images intégrées)."""
    noms: dict[str, str] = {}
    avatars: dict[int, str | None] = {}
    sortie: list[dict] = []
    async with httpx.AsyncClient(follow_redirects=True) as http:
        async def image_uri(url: str, cote: int = IMAGE_COTE_MAX) -> str | None:
            contenu = await _telecharger(http, url)
            if not contenu:
                return None
            try:
                octets, mime = await asyncio.to_thread(reduire_image, contenu, cote)
            except Exception:
                return None
            return data_uri(octets, mime)

        async for msg in salon.history(limit=None, oldest_first=True):
            for u in msg.mentions:
                noms["u" + str(u.id)] = getattr(u, "display_name", None) or u.name
            for r in msg.role_mentions:
                noms["r" + str(r.id)] = r.name
            for c in msg.channel_mentions:
                noms["c" + str(c.id)] = c.name
            auteur = msg.author
            if auteur.id not in avatars:
                try:
                    avatars[auteur.id] = await image_uri(auteur.display_avatar.replace(size=64, static_format="png").url, 64)
                except Exception:
                    avatars[auteur.id] = None
            m = {
                "auteur_id": auteur.id, "auteur": getattr(auteur, "display_name", None) or auteur.name,
                "avatar": avatars[auteur.id], "date": msg.created_at, "modifie": bool(msg.edited_at),
                "texte_html": rendre_texte(msg.content, noms), "images": [], "fichiers": [], "embeds": [],
            }
            if msg.type not in (discord.MessageType.default, discord.MessageType.reply, discord.MessageType.chat_input_command):
                m["systeme"] = True
                m["texte_html"] = html.escape(msg.system_content or "")
                if not m["texte_html"]:
                    continue
            ref = msg.reference.resolved if msg.reference else None
            if isinstance(ref, discord.Message):
                m["reponse_a"] = getattr(ref.author, "display_name", None) or ref.author.name
            for piece in msg.attachments:
                est_image = (piece.content_type or "").startswith("image/") or piece.filename.lower().endswith(EXTENSIONS_IMAGE)
                if est_image:
                    uri = await image_uri(piece.url)
                    if uri:
                        m["images"].append(uri)
                        continue
                uri = None
                contenu = await _telecharger(http, piece.url)
                if contenu:
                    uri = data_uri(contenu, (piece.content_type or "application/octet-stream").split(";")[0])
                m["fichiers"].append((piece.filename, uri, piece.size))
            images_embeds = {}
            for emb in msg.embeds:
                url = (emb.image and emb.image.url) or (emb.thumbnail and emb.thumbnail.url)
                if url and url not in images_embeds:
                    images_embeds[url] = await image_uri(url)
                if emb.type in ("image", "gifv") and url and images_embeds.get(url):
                    m["images"].append(images_embeds[url])  # lien d'image collé : l'image elle-même
                elif emb.title or emb.description or emb.fields or images_embeds.get(url or ""):
                    m["embeds"].append(_embed_html(emb, images_embeds))
            for s in msg.stickers:
                m["texte_html"] += f' <span class="modifie">[autocollant {html.escape(s.name)}]</span>'
            sortie.append(m)
    return sortie


# ---------------------------------------------------------------- base du site

async def enregistrer_creation(pb, salon_id: int, titre: str, createur: discord.Member) -> dict:
    return await pb.creer(COLLECTION, {
        "salon_id": str(salon_id), "titre": titre, "createur_discord_id": str(createur.id),
        "createur_nom": createur.display_name, "statut": "ouvert",
    })


async def deposer_archive(pb, fiche_id: str, chemin: str, nom_fichier: str, champs: dict) -> dict:
    """Envoie le fichier HTML (lu depuis le disque) et les champs d'archive dans la fiche du salon."""
    with open(chemin, "rb") as f:
        resp = await pb._client.patch(
            f"{pb.url}/api/collections/{COLLECTION}/records/{fiche_id}",
            data={k: str(v) for k, v in champs.items()},
            files={"fichier": (nom_fichier, f, "text/html")},
            headers={"Authorization": await pb._auth()}, timeout=600,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Dépôt de l'archive {fiche_id} -> {resp.status_code} {resp.text[:300]}")
    return resp.json()


# ---------------------------------------------------------------- commande

async def trouver_categorie(guild: discord.Guild) -> discord.CategoryChannel | None:
    ident = os.getenv("SALON_MJ_CATEGORIE_ID")
    if ident:
        c = guild.get_channel(int(ident))
        if isinstance(c, discord.CategoryChannel):
            return c
    for c in guild.categories:
        nom = unicodedata.normalize("NFKD", c.name).encode("ascii", "ignore").decode().lower()
        if "coin rp" in re.sub(r"[^a-z ]+", " ", nom).replace("  ", " "):
            return c
    return None


async def creer(eco, membre: discord.Member, titre: str) -> str:
    """Crée le salon et sa fiche ; renvoie le message pour le joueur."""
    titre = (titre or "").strip()[:100]
    if not titre:
        return "Donne un titre au salon."
    categorie = await trouver_categorie(membre.guild)
    if not categorie:
        return "Catégorie « Coin RP » introuvable : préviens un admin."
    droits = dict(categorie.overwrites)
    droits[membre] = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True,
                                                 embed_links=True, read_message_history=True)
    salon = await categorie.create_text_channel(
        nom_salon(titre), overwrites=droits,
        topic=f"{titre} · salon MJ de {membre.display_name}, archivé sur le site 24 h après le dernier message",
        reason=f"/salon_mj par {membre} ({membre.id})",
    )
    try:
        await salon.move(beginning=True, category=categorie, sync_permissions=False)
    except discord.HTTPException:
        log.exception("Salon MJ %s : déplacement en haut de la catégorie impossible", salon.id)
    try:
        await enregistrer_creation(eco.pb, salon.id, titre, membre)
    except Exception:
        log.exception("Salon MJ %s : fiche non enregistrée, suppression du salon", salon.id)
        await salon.delete(reason="Salon MJ : fiche non enregistrée sur le site")
        return "Le salon n'a pas pu être enregistré sur le site : réessaie dans un instant."
    await salon.send(
        f"📜 **{titre}** — salon ouvert par {membre.mention}.\n"
        "Il sera archivé sur le site **24 h après le dernier message**, puis supprimé ; "
        "le lien de l'archive (gardée 30 jours) sera posté dans le salon d'archive.",
        allowed_mentions=discord.AllowedMentions(users=False),
    )
    return f"Salon {salon.mention} créé en haut de **{categorie.name}**."


# ---------------------------------------------------------------- archivage

async def archiver(eco, fiche: dict, salon: discord.TextChannel, maintenant: float | None = None) -> bool:
    """Archive un salon inactif. True si c'est terminé (salon supprimé), False s'il faut retenter."""
    pb = eco.pb
    archives_id = os.getenv("SALON_MJ_ARCHIVES_ID")
    salon_archives = None
    if archives_id:
        salon_archives = eco.client.get_channel(int(archives_id))
        if salon_archives is None:
            try:
                salon_archives = await eco.client.fetch_channel(int(archives_id))
            except discord.HTTPException:
                salon_archives = None
    if salon_archives is None and not fiche.get("cle"):
        log.warning("Salon MJ %s inactif mais SALON_MJ_ARCHIVES_ID absent ou introuvable : archivage reporté", salon.id)
        return False

    cle = fiche.get("cle")
    if not (cle and fiche.get("fichier")):
        messages = await lire_salon(salon)
        maintenant_dt = datetime.fromtimestamp(maintenant or time.time(), timezone.utc)
        info = {
            "titre": fiche.get("titre") or salon.name, "salon": salon.name,
            "createur": fiche.get("createur_nom") or "?",
            "debut": salon.created_at, "fin": messages[-1]["date"] if messages else salon.created_at,
            "genere": maintenant_dt,
        }
        cle = nouvelle_cle()
        dossier = tempfile.mkdtemp(prefix="salon_mj_")
        try:
            ascii_ = unicodedata.normalize("NFKD", nom_salon(info["titre"])).encode("ascii", "ignore").decode()
            nom_fichier = f"archive-{ascii_[:60] or 'salon'}-{info['debut'].astimezone(PARIS):%Y-%m-%d}.html"
            chemin = os.path.join(dossier, nom_fichier)
            with open(chemin, "w", encoding="utf-8") as f:
                f.write(page_html(info, messages))
            taille = os.path.getsize(chemin)
            expire = datetime.fromtimestamp((maintenant or time.time()) + ARCHIVE_JOURS * 86400, timezone.utc)
            fiche = await deposer_archive(pb, fiche["id"], chemin, nom_fichier, {
                "cle": cle, "statut": "archive", "taille": taille,
                "nb_messages": sum(1 for m in messages if not m.get("systeme")),
                "debut": info["debut"].strftime("%Y-%m-%d %H:%M:%S.000Z"),
                "fin": info["fin"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z"),
                "expire": expire.strftime("%Y-%m-%d %H:%M:%S.000Z"),
            })
        finally:
            import shutil
            shutil.rmtree(dossier, ignore_errors=True)

    if salon_archives is None:
        log.warning("Salon MJ %s archivé (clé %s) mais aucun salon d'archive : salon Discord gardé", salon.id, cle)
        return False
    if not fiche.get("annonce_id"):
        taille = int(fiche.get("taille") or 0)
        lien = lien_archive(cle)
        embed = discord.Embed(
            title=f"📜 {fiche.get('titre') or salon.name}", url=lien, color=0xD6B36A,
            description=(f"Archive du salon **#{salon.name}** ouvert par <@{fiche.get('createur_discord_id')}>.\n"
                         f"{fiche.get('nb_messages') or 0} message(s) · {taille / 1024 ** 2:.1f} Mo\n\n"
                         f"[Lire l'archive]({lien}) · [Télécharger]({lien}?telecharger=1)"),
        )
        expire = fiche.get("expire") or ""
        if expire:
            try:
                dt = datetime.fromisoformat(expire.replace(" ", "T").replace("Z", "+00:00"))
                embed.set_footer(text=f"Lien valable jusqu'au {dt.astimezone(PARIS):%d/%m/%Y}")
            except ValueError:
                pass
        annonce = await salon_archives.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await pb.maj(COLLECTION, fiche["id"], {"annonce_id": str(annonce.id)})
    await salon.delete(reason="Salon MJ inactif depuis 24 h : archivé sur le site")
    await pb.maj(COLLECTION, fiche["id"], {"statut": "archive", "supprime_le": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")})
    log.info("Salon MJ %s archivé et supprimé (clé %s…)", salon.id, cle[:6])
    return True


async def balayer(eco, maintenant: float | None = None) -> None:
    pb = eco.pb
    maintenant = maintenant or time.time()
    for fiche in await pb.lister(COLLECTION, filtre='statut="ouvert" || (statut="archive" && supprime_le="")'):
        try:
            salon = eco.client.get_channel(int(fiche["salon_id"]))
            try:
                salon = await eco.client.fetch_channel(int(fiche["salon_id"]))  # dernier message à jour
            except discord.NotFound:
                await pb.maj(COLLECTION, fiche["id"], {"statut": "disparu"})
                log.info("Salon MJ %s supprimé à la main : fiche marquée « disparu »", fiche["salon_id"])
                continue
            dernier = heure_snowflake(salon.last_message_id or salon.id)
            if maintenant - dernier < INACTIVITE_S:
                continue
            await archiver(eco, fiche, salon, maintenant)
        except Exception:
            log.exception("Salon MJ %s : archivage en échec, nouvel essai au prochain balayage", fiche.get("salon_id"))
    # Archives périmées : la fiche et son fichier sont effacés de la base du site.
    limite = datetime.fromtimestamp(maintenant, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for fiche in await pb.lister(COLLECTION, filtre=f'expire!="" && expire<"{limite}"'):
        try:
            await pb.supprimer(COLLECTION, fiche["id"])
            log.info("Archive du salon MJ « %s » expirée : effacée", fiche.get("titre"))
        except Exception:
            log.exception("Archive %s : effacement en échec", fiche.get("id"))


async def boucle(eco) -> None:
    await eco.client.wait_until_ready()
    while True:
        try:
            await balayer(eco)
        except Exception:
            log.exception("Balayage des salons MJ en échec")
        await asyncio.sleep(INTERVALLE_S)
