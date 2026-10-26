"""Welcome image generation with Pillow.

Layout (1200x500):
  - Full canvas: banner background (default banner, custom upload, or a gradient).
  - A dark rounded card on the left holds the avatar, WELCOME, username,
    server name and member number. The card stops at x=900 so it never covers
    the logo in the bottom-right corner of the default banner.

Every image object is closed as soon as it is no longer needed and nothing
is cached, so memory is released right after each render.
"""
import io
import logging
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps

import config

log = logging.getLogger(__name__)

RESAMPLE = Image.Resampling.LANCZOS
W, H = config.IMAGE_WIDTH, config.IMAGE_HEIGHT

# --- Layout constants ------------------------------------------------------
CARD_BOX = (40, 128, 900, 386)
AVATAR_SIZE = 190
AVATAR_X, AVATAR_Y = 80, 162
TEXT_X = 318
TEXT_MAX_WIDTH = CARD_BOX[2] - 40 - TEXT_X               # 542 px

# --- Colours ---------------------------------------------------------------
RED = (220, 30, 45, 255)
WHITE = (255, 255, 255, 255)
SOFT_WHITE = (232, 235, 242, 255)
SOFT_RED = (255, 118, 126, 255)
DARK = (14, 16, 22, 255)


class BackgroundError(ValueError):
    """Raised when an uploaded background cannot be used."""


# --- Fonts -----------------------------------------------------------------
def _font_path(bold: bool) -> Optional[Path]:
    """Pick a font file from assets/fonts, preferring the requested weight."""
    if not config.FONT_DIR.is_dir():
        return None
    files = sorted(p for p in config.FONT_DIR.iterdir() if p.suffix.lower() in {".ttf", ".otf"})
    if not files:
        return None
    is_bold = [p for p in files if "bold" in p.stem.lower()]
    not_bold = [p for p in files if "bold" not in p.stem.lower()]
    preferred = (is_bold or not_bold) if bold else (not_bold or is_bold)
    return preferred[0]


def _load_font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    path = _font_path(bold)
    if path is not None:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            log.warning("Could not load font %s, using the built-in font", path)
    return ImageFont.load_default(size)  # no CJK glyphs, but never crashes


def _text_width(font: ImageFont.FreeTypeFont, text: str, spacing: int = 0) -> float:
    if spacing == 0:
        return font.getlength(text)
    return sum(font.getlength(ch) for ch in text) + spacing * max(len(text) - 1, 0)


def _fit_text(bold: bool, text: str, base_size: int, max_width: int, min_size: int,
              spacing: int = 0) -> tuple[ImageFont.FreeTypeFont, str]:
    """Shrink the font until the text fits; add an ellipsis if it still does not."""
    size = base_size
    while True:
        font = _load_font(bold, size)
        width = _text_width(font, text, spacing)
        if width <= max_width:
            return font, text
        if size <= min_size:
            break
        size = max(min_size, min(size - 2, int(size * max_width / width)))
    while len(text) > 1 and _text_width(font, text + "…", spacing) > max_width:
        text = text[:-1]
    return font, text.rstrip() + "…"


def _draw_spaced(draw: ImageDraw.ImageDraw, x: float, y: float, text: str,
                 font: ImageFont.FreeTypeFont, fill, spacing: int) -> None:
    """Draw text left-aligned (vertically centred on y) with extra letter spacing."""
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill, anchor="lm")
        x += font.getlength(ch) + spacing


# --- Image helpers ---------------------------------------------------------
def _circle_mask(diameter: int) -> Image.Image:
    """Anti-aliased circular mask (drawn at 4x, then scaled down)."""
    scale = 4
    big = Image.new("L", (diameter * scale, diameter * scale), 0)
    ImageDraw.Draw(big).ellipse((0, 0, diameter * scale - 1, diameter * scale - 1), fill=255)
    mask = big.resize((diameter, diameter), RESAMPLE)
    big.close()
    return mask


def _paste_disc(canvas: Image.Image, x: int, y: int, diameter: int, colour) -> None:
    mask = _circle_mask(diameter)
    canvas.paste(colour, (x, y, x + diameter, y + diameter), mask)
    mask.close()


def _gradient_banner() -> Image.Image:
    """Simple dark vertical gradient, used when no background image is available."""
    gradient = Image.linear_gradient("L").resize((W, H))
    banner = ImageOps.colorize(gradient, black=(24, 28, 48), white=(78, 40, 92))
    gradient.close()
    return banner


def _load_banner(background_path: Optional[Path]) -> Image.Image:
    """Load the custom background, else the default banner, else a gradient."""
    for candidate in (background_path, config.DEFAULT_BACKGROUND):
        if candidate is None or not Path(candidate).is_file():
            continue
        try:
            with Image.open(candidate) as source:
                return ImageOps.fit(source.convert("RGB"), (W, H), RESAMPLE)
        except (OSError, ValueError):
            log.warning("Could not read background %s", candidate)
    return _gradient_banner()


def _circle_avatar(avatar_bytes: Optional[bytes], diameter: int) -> Image.Image:
    avatar = None
    if avatar_bytes:
        try:
            with Image.open(io.BytesIO(avatar_bytes)) as source:
                avatar = ImageOps.fit(source.convert("RGBA"), (diameter, diameter), RESAMPLE)
        except (OSError, ValueError):
            log.warning("Could not decode avatar image, using a placeholder")
    if avatar is None:
        avatar = Image.new("RGBA", (diameter, diameter), (88, 101, 242, 255))
    mask = _circle_mask(diameter)
    avatar.putalpha(mask)
    mask.close()
    return avatar


# --- Public API ------------------------------------------------------------
def build_welcome_image(avatar_bytes: Optional[bytes], username: str, server_name: str,
                        member_count: int, background_path: Optional[Path] = None) -> io.BytesIO:
    """Render the welcome image and return it as an in-memory PNG."""
    canvas = Image.new("RGBA", (W, H), DARK)
    try:
        # 1. Background banner
        banner = _load_banner(background_path)
        canvas.paste(banner, (0, 0))
        banner.close()

        # 2. Semi-transparent rounded card
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rounded_rectangle(
            CARD_BOX, radius=28, fill=(10, 12, 18, 178), outline=(255, 255, 255, 56), width=2)
        merged = Image.alpha_composite(canvas, overlay)
        overlay.close()
        canvas.close()
        canvas = merged

        # 3. Avatar with a red ring and a white inner ring
        _paste_disc(canvas, AVATAR_X - 9, AVATAR_Y - 9, AVATAR_SIZE + 18, RED)
        _paste_disc(canvas, AVATAR_X - 5, AVATAR_Y - 5, AVATAR_SIZE + 10, WHITE)
        avatar = _circle_avatar(avatar_bytes, AVATAR_SIZE)
        canvas.paste(avatar, (AVATAR_X, AVATAR_Y), avatar)
        avatar.close()

        draw = ImageDraw.Draw(canvas)

        # 4. Text block inside the card
        _draw_spaced(draw, TEXT_X, 165, "WELCOME", _load_font(True, 30), SOFT_RED, 7)

        font, text = _fit_text(True, username, 58, TEXT_MAX_WIDTH, 30)
        draw.text((TEXT_X, 228), text, font=font, fill=WHITE, anchor="lm")

        draw.rounded_rectangle((TEXT_X, 270, TEXT_X + 80, 274), radius=2, fill=RED)

        font, text = _fit_text(False, f"to {server_name}", 32, TEXT_MAX_WIDTH, 20)
        draw.text((TEXT_X, 308), text, font=font, fill=SOFT_WHITE, anchor="lm")

        font = _load_font(False, 28)
        draw.text((TEXT_X, 350), f"Member #{member_count:,}", font=font, fill=SOFT_RED, anchor="lm")

        # 5. Encode, then release the canvas immediately
        rgb = canvas.convert("RGB")
        buffer = io.BytesIO()
        rgb.save(buffer, format="PNG", compress_level=6)
        rgb.close()
        buffer.seek(0)
        return buffer
    finally:
        canvas.close()


def save_custom_background(data: bytes, destination: Path) -> None:
    """Validate an uploaded PNG/JPG, pre-fit it to the banner size and store it as JPEG.

    Storing the already-cropped result keeps files small and rendering fast.
    """
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG"}:
                raise BackgroundError("Only PNG and JPG images are supported.")
            if source.width * source.height > config.MAX_BACKGROUND_PIXELS:
                raise BackgroundError("That image has too many pixels. Please use one under about 12 megapixels.")
            source.draft("RGB", (W * 2, H * 2))  # faster, lower-memory JPEG decode
            fitted = ImageOps.fit(source.convert("RGB"), (W, H), RESAMPLE)
    except BackgroundError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise BackgroundError("That file could not be read as a PNG or JPG image.") from exc

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination.with_name(destination.name + ".tmp")
        fitted.save(temp_path, format="JPEG", quality=90, optimize=True)
        temp_path.replace(destination)
    except OSError as exc:
        raise BackgroundError("The background could not be saved on disk.") from exc
    finally:
        fitted.close()
