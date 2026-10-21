"""Phases des deux lunes (Davos la rouge, Amarante), copie du calcul du site (src/lib/lunesCalcul.ts, repris du
masterPage.js de Wix) : jour lunaire en UTC pur, date du jour ramenée à l'année 1255, compté depuis le 1er janvier -7000
(calendrier grégorien proleptique, comme Date.UTC en JavaScript). tests/lunes_reference.json est produit par le site
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


def _jour_utc(quand: datetime | date | None) -> date:
    if quand is None:
        quand = datetime.now(timezone.utc)
    if isinstance(quand, datetime):
        quand = (quand if quand.tzinfo else quand.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).date()
    return quand


def jour_lunaire(quand: datetime | date | None = None) -> int:
    d = _jour_utc(quand)
    # Date.UTC(1255, mois, jour) : un 29 février devient le 1er mars (1255 n'est pas bissextile).
    return _jours_civils(1255, d.month, 1) + d.day - 1 - _REF


def pourcentage(nom: str, j: int) -> float:
    if nom == DAVOS:
        return (math.sin((j + 1) * 2 * math.pi / 13) + 1) / 2 * 100
    return (math.sin((j + 1) * 2.1 * math.pi / 43) + 1) / 2 * 100


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
