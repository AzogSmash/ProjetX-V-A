"""Rendu isolé d'une course casino.

Ce module ne connaît ni Discord ni les soldes : il reçoit le résultat officiel
déjà figé et produit seulement une vidéo qui le met en scène.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT, FPS, DURATION_SECONDS = 960, 540, 12, 9
MAX_VIDEO_BYTES = 8 * 1024 * 1024
TRACK_ASSET = Path(__file__).with_name("assets") / "race" / "track.png"

# Coordonnées explicites du centre de piste, dans les proportions de track.png.
# Le circuit peut être remplacé sans détection de pixels : il suffit d'adapter
# cette liste de waypoints.
TRACK_PATH = [
    (0.496, 0.712), (0.360, 0.728), (0.170, 0.720), (0.070, 0.610),
    (0.060, 0.430), (0.105, 0.265), (0.245, 0.175), (0.500, 0.166),
    (0.770, 0.180), (0.920, 0.285), (0.940, 0.470), (0.885, 0.640),
    (0.700, 0.725), (0.496, 0.712),
]
CAR_COLORS = [(231, 76, 60), (52, 152, 219), (241, 196, 15), (46, 204, 113), (155, 89, 182)]


def _font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _point_at(progress: float) -> tuple[float, float, float]:
    """Position et angle sur TRACK_PATH ; progress est autorisé hors 0..1."""
    progress %= 1.0
    scaled = progress * (len(TRACK_PATH) - 1)
    index = min(int(scaled), len(TRACK_PATH) - 2)
    fraction = scaled - index
    x1, y1 = TRACK_PATH[index]
    x2, y2 = TRACK_PATH[index + 1]
    x = x1 + (x2 - x1) * fraction
    y = y1 + (y2 - y1) * fraction
    return x * WIDTH, y * HEIGHT, math.atan2(y2 - y1, x2 - x1)


def _ease(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3 - 2 * value)


def visual_progress(frame_ratio: float, rank: int, grid_position: int) -> float:
    """Progression expressive, mais qui finit obligatoirement dans final_order."""
    race = _ease(frame_ratio)
    final_progress = 1.018 - rank * 0.040
    start_progress = -0.012 * grid_position
    base = start_progress + (final_progress - start_progress) * race
    # Les oscillations donnent des dépassements pendant la course et disparaissent
    # entièrement à l'arrivée : le classement officiel reste visible et exact.
    excitement = math.sin(frame_ratio * math.pi * 5 + grid_position * 1.7) * 0.045 * (1 - race)
    return base + excitement


def _draw_car(canvas: Image.Image, x: float, y: float, angle: float, color, number: int):
    car = Image.new("RGBA", (32, 18), (0, 0, 0, 0))
    draw = ImageDraw.Draw(car)
    draw.rounded_rectangle((2, 3, 30, 15), radius=5, fill=(*color, 255), outline=(255, 255, 255, 255), width=1)
    draw.rectangle((10, 5, 22, 13), fill=(30, 35, 45, 255))
    draw.text((4, 2), str(number), fill=(255, 255, 255, 255), font=_font(9))
    car = car.rotate(-math.degrees(angle), resample=Image.Resampling.BICUBIC, expand=True)
    canvas.alpha_composite(car, (int(x - car.width / 2), int(y - car.height / 2)))


def _draw_hud(draw: ImageDraw.ImageDraw, drivers, final_order, frame_ratio: float, race_number: int):
    draw.rounded_rectangle((12, 12, 270, 158), radius=10, fill=(12, 16, 26, 220), outline=(255, 255, 255, 80), width=1)
    draw.text((24, 23), f"COURSE #{race_number}", fill="white", font=_font(18))
    draw.text((24, 48), "ARRIVÉE" if frame_ratio >= .98 else "COURSE EN DIRECT", fill=(255, 210, 80), font=_font(13))
    title = "CLASSEMENT FINAL" if frame_ratio >= .98 else "CLASSEMENT VISUEL"
    draw.text((24, 70), title, fill=(190, 210, 230), font=_font(12))
    ranks = list(final_order) if frame_ratio >= .98 else sorted(
        range(len(drivers)), key=lambda i: visual_progress(frame_ratio, final_order.index(i), i), reverse=True
    )
    for position, driver_index in enumerate(ranks[:5], 1):
        name = drivers[driver_index]["name"].split()[0][:16]
        color = CAR_COLORS[driver_index % len(CAR_COLORS)]
        draw.rectangle((25, 91 + (position - 1) * 12, 32, 98 + (position - 1) * 12), fill=color)
        draw.text((38, 88 + (position - 1) * 12), f"{position}. {name}", fill="white", font=_font(11))


def render_race_video(output_path: str | Path, drivers: list[dict], final_order: list[int], race_number: int = 1) -> Path:
    """Produit un MP4 court. Lève une exception exploitable par le fallback Discord."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg indisponible")
    if not TRACK_ASSET.exists():
        raise RuntimeError("Asset de circuit introuvable")
    output = Path(output_path)
    with Image.open(TRACK_ASSET) as source:
        background = source.convert("RGBA").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    with tempfile.TemporaryDirectory(prefix="casino-race-") as temp_dir:
        frames_pattern = str(Path(temp_dir) / "frame-%03d.png")
        total_frames = FPS * DURATION_SECONDS
        for frame in range(total_frames):
            ratio = frame / (total_frames - 1)
            canvas = background.copy()
            for grid_position, driver_index in enumerate(range(len(drivers))):
                rank = final_order.index(driver_index)
                x, y, angle = _point_at(visual_progress(ratio, rank, grid_position))
                _draw_car(canvas, x, y, angle, CAR_COLORS[driver_index % len(CAR_COLORS)], driver_index + 1)
            draw = ImageDraw.Draw(canvas, "RGBA")
            _draw_hud(draw, drivers, final_order, ratio, race_number)
            canvas.convert("RGB").save(Path(temp_dir) / f"frame-{frame:03d}.png", optimize=True)
        command = [
            "ffmpeg", "-y", "-framerate", str(FPS), "-i", frames_pattern,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-crf", "27", str(output),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg a échoué : {result.stderr[-400:]}")
    if not output.exists() or output.stat().st_size > MAX_VIDEO_BYTES:
        raise RuntimeError("Vidéo absente ou trop volumineuse pour Discord")
    return output
