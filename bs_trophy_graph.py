"""Rendu local et déterministe des courbes de trophées Brawl Stars.

Les données restent dans Supabase : ce module ne fait ni appel réseau ni
accès Discord, ce qui permet de consulter l'historique même si l'API BS est
indisponible.
"""

from __future__ import annotations

import io
import re
from datetime import date

from PIL import Image, ImageDraw, ImageFont


CANVAS_SIZE = (1200, 675)
VALID_PERIODS = frozenset({7, 14, 30, 60, 90})


def parse_trophygraph_args(target_or_days: str | None, days: str | None) -> tuple[str | None, int | None, str | None]:
    """Parse la syntaxe prefixe ``[@membre] [jours]`` sans dépendre de Discord."""
    if target_or_days is None:
        return None, 30, None
    if target_or_days.isdigit():
        if days is not None:
            return None, None, "Syntaxe : `!trophygraph [@membre] [7|14|30|60|90]`."
        period = int(target_or_days)
        if period not in VALID_PERIODS:
            return None, None, "Période invalide. Choisis 7, 14, 30, 60 ou 90 jours."
        return None, period, None
    mention = re.fullmatch(r"<@!?(\d+)>", target_or_days)
    if not mention:
        return None, None, "Indique un membre avec `@membre` ou une période : `!trophygraph @membre 30`."
    period = 30 if days is None else int(days) if days.isdigit() else None
    if period not in VALID_PERIODS:
        return None, None, "Période invalide. Choisis 7, 14, 30, 60 ou 90 jours."
    return mention.group(1), period, None


def trophy_summary(points: list[dict]) -> dict[str, int]:
    """Calcule le résumé sans lisser les variations intermédiaires."""
    values = [int(point["trophies"]) for point in points]
    return {
        "start": values[0],
        "current": values[-1],
        "minimum": min(values),
        "maximum": max(values),
        "delta": values[-1] - values[0],
    }


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = (["DejaVuSans-Bold.ttf", "arialbd.ttf"] if bold else ["DejaVuSans.ttf", "arial.ttf"])
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _label(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def render_trophy_graph(player_name: str, points: list[dict], requested_days: int) -> io.BytesIO:
    """Produit un PNG Discord lisible à partir de snapshots réels uniquement."""
    if len(points) < 2:
        raise ValueError("Au moins deux snapshots sont nécessaires.")

    summary = trophy_summary(points)
    image = Image.new("RGB", CANVAS_SIZE, "#101827")
    draw = ImageDraw.Draw(image)
    title_font = _font(38, bold=True)
    stat_font = _font(22, bold=True)
    small_font = _font(18)

    draw.text((55, 38), f"Progression de {player_name}", font=title_font, fill="#f8fafc")
    delta = summary["delta"]
    delta_text = f"{delta:+,}".replace(",", " ")
    stats = [
        ("Début", _label(summary["start"])),
        ("Actuel", _label(summary["current"])),
        ("Variation", delta_text),
        ("Min.", _label(summary["minimum"])),
        ("Max.", _label(summary["maximum"])),
    ]
    for index, (label, value) in enumerate(stats):
        x = 55 + index * 225
        draw.text((x, 100), label, font=small_font, fill="#94a3b8")
        color = "#4ade80" if label == "Variation" and delta >= 0 else "#fb7185" if label == "Variation" else "#f8fafc"
        draw.text((x, 124), value, font=stat_font, fill=color)

    left, top, right, bottom = 100, 205, 1140, 570
    values = [int(point["trophies"]) for point in points]
    minimum, maximum = min(values), max(values)
    padding = max(10, round((maximum - minimum) * 0.12))
    lower, upper = minimum - padding, maximum + padding
    if lower == upper:
        lower -= 10
        upper += 10

    for tick in range(5):
        y = top + (bottom - top) * tick / 4
        value = round(upper - (upper - lower) * tick / 4)
        draw.line((left, y, right, y), fill="#263448", width=1)
        draw.text((18, y - 10), _label(value), font=small_font, fill="#94a3b8")

    coords = []
    for index, value in enumerate(values):
        x = left + (right - left) * index / (len(values) - 1)
        y = bottom - (value - lower) / (upper - lower) * (bottom - top)
        coords.append((x, y))
    draw.line(coords, fill="#60a5fa", width=5, joint="curve")
    for x, y in coords:
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill="#dbeafe", outline="#60a5fa", width=2)

    first_date = date.fromisoformat(points[0]["snapshot_date"]).strftime("%d/%m")
    last_date = date.fromisoformat(points[-1]["snapshot_date"]).strftime("%d/%m")
    draw.text((left, 590), first_date, font=small_font, fill="#94a3b8")
    end_box = draw.textbbox((0, 0), last_date, font=small_font)
    draw.text((right - (end_box[2] - end_box[0]), 590), last_date, font=small_font, fill="#94a3b8")
    draw.text((55, 628), f"{len(points)} jours d'historique réel · période demandée : {requested_days} jours", font=small_font, fill="#94a3b8")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer
