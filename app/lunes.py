"""Phases des deux lunes (Davos la rouge, Amarante), copie du calcul du site (src/lib/lunesCalcul.ts, d'après le
masterPage.js de Wix, revu le 11/11/2026) : jour lunaire CONTINU, compté depuis le 1er janvier -7000 (calendrier grégorien
proleptique, comme Date.UTC en JavaScript) jusqu'à la date en jeu — le 1er janvier 2026 réel est le 1er Sanfio 1257,
le 10/11/2026 le 10 Amoria 1257 (j = 3016121) — puis +1 par jour réel UTC. Périodes : Davos 12,8 jours, Amarante 42,4. tests/lunes_reference.json est produit par le site
(tests/lunes.test.ts) : tests/test_lunes.py vérifie que les deux calculs donnent les mêmes phases."""
import math
from datetime import date, datetime, timedelta, timezone

DAVOS, AMARANTE = "Davos", "Amarante"
# Valeur du champ ros_recompenses.disponibilite -> lune dont la pleine lune ouvre l'article.
DISPONIBILITES = {"pleine_lune_davos": DAVOS, "pleine_lune_amarante": AMARANTE}


def _jours_civils(a: int, m: int, j: int) -> int:
    """Jours depuis le 1970-01-01 (grégorien proleptique, années négatives comprises ; algorithme de H. Hinnant)."""
    a -= m <= 2
    ere = a // 400  # division entière arrondie vers le bas en Python, années négatives comprises
    ae = a - ere * 400
    jda = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + j - 1
    jde = ae * 365 + ae // 4 - ae // 100 + jda
    return ere * 146097 + jde - 719468


_REF = _jours_civils(-7000, 1, 1)
# Jours du 1er janvier -7000 au 1er janvier 1257 (= 1er janvier 2026 réel).
J_ANCRE = _jours_civils(1257, 1, 1) - _REF
_ANCRE_REELLE = date(2026, 1, 1)


def _jour_utc(quand: datetime | date | None) -> date:
    if quand is None:
        quand = datetime.now(timezone.utc)
    if isinstance(quand, datetime):
        quand = (quand if quand.tzinfo else quand.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).date()
    return quand


def jour_lunaire(quand: datetime | date | None = None) -> int:
    return J_ANCRE + (_jour_utc(quand) - _ANCRE_REELLE).days


def pourcentage(nom: str, j: float) -> float:
    if nom == DAVOS:
        return (math.sin((j + 1) * 2 * math.pi / 12.8) + 1) / 2 * 100
    return (math.sin((j + 1) * 2 * math.pi / 42.4) + 1) / 2 * 100


def phase(cur: float, prev: float, nxt: float) -> str:
    if cur < 6:
        return "Nouvelle lune"
    if cur > 95:
        return "Pleine lune"
    if prev < cur < nxt:
        return "Phase croissante"
    if prev > cur > nxt:
        return "Phase décroissante"
    return "Phase inconnue"


def etat(nom: str, quand: datetime | date | None = None) -> tuple[float, str]:
    j = jour_lunaire(quand)
    cur = pourcentage(nom, j)
    return cur, phase(cur, pourcentage(nom, j - 1), pourcentage(nom, j + 1))


def est_pleine(nom: str, quand: datetime | date | None = None) -> bool:
    return etat(nom, quand)[1] == "Pleine lune"


def disponible(article: dict, quand: datetime | date | None = None) -> bool:
    """Un article « pleine lune » n'est disponible que pendant la pleine lune de sa lune ; les autres, toujours."""
    lune = DISPONIBILITES.get(article.get("disponibilite") or "")
    return lune is None or est_pleine(lune, quand)


def pleine_lune_de(lune: str) -> str:
    return f"Pleine lune d'{lune}" if lune[:1] in "AEIOUÉ" else f"Pleine lune de {lune}"


def mention(article: dict) -> str:
    """« 🌕 Pleine lune de Davos » / « 🌕 Pleine lune d'Amarante » pour un article de pleine lune, sinon ""."""
    lune = DISPONIBILITES.get(article.get("disponibilite") or "")
    return f"🌕 {pleine_lune_de(lune)}" if lune else ""


def prochaines_pleines_lunes(nom: str, depuis: datetime | date | None = None, n: int = 3, horizon: int = 800) -> list[tuple[date, date]]:
    """Périodes de pleine lune (premier et dernier jour UTC), celle en cours comprise."""
    d0 = _jour_utc(depuis)
    out, cours = [], None
    for i in range(horizon):
        if len(out) >= n:
            break
        d = d0 + timedelta(days=i)
        if est_pleine(nom, d):
            cours = (cours[0], d) if cours else (d, d)
        elif cours:
            out.append(cours)
            cours = None
    if cours and len(out) < n:
        out.append(cours)
    return out


def instant(nom: str, quand: datetime | None = None) -> tuple[float, bool, str]:
    """Pourcentage à l'heure près (comme l'en-tête du site : jf = j + heure UTC / 24 - 0,5), sens (croissante) et phase
    écrite du jour."""
    if quand is None:
        quand = datetime.now(timezone.utc)
    quand = (quand if quand.tzinfo else quand.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    j = jour_lunaire(quand)
    jf = j + (quand.hour + quand.minute / 60 + quand.second / 3600) / 24 - 0.5
    cur = pourcentage(nom, jf)
    return cur, pourcentage(nom, jf + 0.05) >= pourcentage(nom, jf - 0.05), etat(nom, quand)[1]


def emoji_phase(p: float, croissante: bool, phase_jour: str = "") -> str:
    if phase_jour == "Nouvelle lune" or (not phase_jour and p < 6):
        return "🌑"
    if phase_jour == "Pleine lune" or (not phase_jour and p > 95):
        return "🌕"
    if croissante:
        return "🌒" if p < 35 else "🌓" if p <= 65 else "🌔"
    return "🌘" if p < 35 else "🌗" if p <= 65 else "🌖"


def texte_statut(quand: datetime | None = None) -> str:
    """Statut Discord du bot : « 🌔 Davos 40 % · 🌖 Amarante 35 % », avec « (Pleine lune) » / « (Nouvelle lune) »
    pour une lune pleine ou nouvelle ce jour-là (moins de 128 caractères)."""
    parts = []
    for nom in (DAVOS, AMARANTE):
        p, croissante, ph = instant(nom, quand)
        texte = f"{emoji_phase(p, croissante, ph)} {nom} {round(p)} %"
        if ph in ("Pleine lune", "Nouvelle lune"):
            texte += f" ({ph})"
        parts.append(texte)
    return " · ".join(parts)[:128]
