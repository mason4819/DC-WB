"""Welcome image generation with Pillow.

Layout (1200x406, the same ratio as the default banner):
  - Background banner (default banner, custom upload, or a simple gradient).
  - A dark rounded card on the left holds the avatar, WELCOME, display name,
    server name and member number. The card stops at x=900 so it never covers
    the logo in the bottom-right corner of the default banner.

Names from Discord can contain fancy Unicode, emoji or symbols. Text is
normalised and every character is drawn with the first font in assets/fonts
that really has a glyph for it, so no "tofu" boxes appear. Characters that no
font supports are dropped instead of being drawn as boxes.

Every image object is closed as soon as it is no longer needed and nothing
is cached, so memory is released right after each render.
"""
import asyncio
import io
import logging
import unicodedata
from pathlib import Path
from typing import Optional, Union

from PIL import Image, ImageDraw, ImageFont, ImageOps

import config

log = logging.getLogger(__name__)

RESAMPLE = Image.Resampling.LANCZOS

# One image at a time per process (local requests and requests from the other host
# share this lock), which caps CPU and RAM during bursts.
RENDER_LOCK = asyncio.Semaphore(1)

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

# Font families are tried in this order. Any other font file placed in
# assets/fonts is tried afterwards (alphabetical), so adding a font is just
# copying a .ttf/.otf file into that folder.
FONT_PRIORITY = ("NotoSansTC", "NotoSansSC", "NotoSansJP", "NotoSansKR",
                 "NotoSansSymbols", "NotoSansSymbols2", "NotoEmoji")

Glyph = tuple[str, ImageFont.FreeTypeFont]


class BackgroundError(ValueError):
    """Raised when an uploaded background cannot be used."""


# --- Fonts -----------------------------------------------------------------
def _family(path: Path) -> str:
    return path.stem.split("-")[0].split("[")[0]


def _font_paths(bold: bool) -> list[Path]:
    """One file per font family, ordered by priority. Uses the bold file if one exists."""
    if not config.FONT_DIR.is_dir():
        return []
    families: dict[str, list[Path]] = {}
    for path in sorted(config.FONT_DIR.iterdir()):
        if path.suffix.lower() in {".ttf", ".otf"}:
            families.setdefault(_family(path), []).append(path)

    chosen: dict[str, Path] = {}
    for name, files in families.items():
        bold_files = [p for p in files if "bold" in p.stem.lower()]
        plain_files = [p for p in files if "bold" not in p.stem.lower()]
        chosen[name] = (bold_files or plain_files)[0] if bold else (plain_files or bold_files)[0]

    ordered = [chosen[name] for name in FONT_PRIORITY if name in chosen]
    ordered += [path for name, path in chosen.items() if name not in FONT_PRIORITY]
    return ordered


class _FontSet:
    """Fonts for one weight and size. Faces are opened lazily, only when a character needs them."""

    def __init__(self, bold: bool, size: int) -> None:
        self.size = size
        self._paths = _font_paths(bold)
        self._fonts: dict[int, Optional[ImageFont.FreeTypeFont]] = {}
        self._notdef: dict[int, bytes] = {}
        self._support: dict[tuple[int, str], bool] = {}
        self.primary = self._open(0)
        if self.primary is None:
            self.primary = ImageFont.load_default(size)  # no CJK glyphs, but never crashes

    def _open(self, index: int) -> Optional[ImageFont.FreeTypeFont]:
        if index >= len(self._paths):
            return None
        if index not in self._fonts:
            try:
                self._fonts[index] = ImageFont.truetype(str(self._paths[index]), self.size)
            except OSError:
                log.warning("Could not load font %s", self._paths[index])
                self._fonts[index] = None
        return self._fonts[index]

    def _has_glyph(self, index: int, font: ImageFont.FreeTypeFont, ch: str) -> bool:
        """A character is missing if it renders exactly like an unassigned code point."""
        key = (index, ch)
        if key not in self._support:
            if index not in self._notdef:
                self._notdef[index] = bytes(font.getmask("\uffff"))
            self._support[key] = bytes(font.getmask(ch)) != self._notdef[index]
        return self._support[key]

    def font_for(self, ch: str) -> Optional[ImageFont.FreeTypeFont]:
        """First font that really contains this character, or None."""
        if ch == " ":
            return self.primary
        for index in range(len(self._paths)):
            font = self._open(index)
            if font is not None and self._has_glyph(index, font, ch):
                return font
        return None

    def layout(self, text: str) -> list[Glyph]:
        glyphs: list[Glyph] = []
        for ch in text:
            font = self.font_for(ch)
            if font is not None:
                glyphs.append((ch, font))
        return glyphs

    def baseline(self, centre_y: float) -> float:
        """Baseline that vertically centres text of the primary font on centre_y."""
        ascent, descent = self.primary.getmetrics()
        return centre_y + (ascent - descent) / 2


def sanitize_text(text: str) -> str:
    """Normalise fancy Unicode and drop characters that cannot be drawn sensibly.

    NFKC turns styled letters (e.g. mathematical script, full-width) into plain ones.
    Combining marks, zero-width characters and control characters are removed.
    """
    text = unicodedata.normalize("NFKC", text)
    kept = [ch for ch in text if unicodedata.category(ch) not in {"Cc", "Cf", "Mn", "Me", "Cs", "Cn"}]
    return " ".join("".join(kept).split())


def _glyphs_width(glyphs: list[Glyph], spacing: int = 0) -> float:
    return sum(font.getlength(ch) for ch, font in glyphs) + spacing * max(len(glyphs) - 1, 0)


def _fit_text(bold: bool, text: str, base_size: int, max_width: int, min_size: int,
              spacing: int = 0) -> tuple[_FontSet, list[Glyph]]:
    """Shrink the font until the text fits; add an ellipsis if it still does not."""
    size = base_size
    while True:
        fonts = _FontSet(bold, size)
        glyphs = fonts.layout(text)
        width = _glyphs_width(glyphs, spacing)
        if width <= max_width:
            return fonts, glyphs
        if size <= min_size:
            break
        size = max(min_size, min(size - 2, int(size * max_width / width)))

    ellipsis = fonts.layout("…") or fonts.layout("...")
    while glyphs and _glyphs_width(glyphs + ellipsis, spacing) > max_width:
        glyphs.pop()
    return fonts, glyphs + ellipsis


def _draw_glyphs(draw: ImageDraw.ImageDraw, x: float, baseline: float, glyphs: list[Glyph],
                 fill, spacing: int = 0) -> None:
    """Draw glyph by glyph on a shared baseline so mixed fonts line up."""
    for ch, font in glyphs:
        draw.text((x, baseline), ch, font=font, fill=fill, anchor="ls")
        x += font.getlength(ch) + spacing


def _draw_line(draw: ImageDraw.ImageDraw, x: float, centre_y: float, text: str, bold: bool,
               base_size: int, min_size: int, fill, max_width: int = TEXT_MAX_WIDTH,
               spacing: int = 0) -> bool:
    """Fit and draw one line. Returns False if nothing drawable was left."""
    fonts, glyphs = _fit_text(bold, text, base_size, max_width, min_size, spacing)
    if not glyphs:
        return False
    _draw_glyphs(draw, x, fonts.baseline(centre_y), glyphs, fill, spacing)
    return True


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


def _load_banner(background: Union[Path, bytes, None]) -> Image.Image:
    """Load the custom background (file path or raw bytes), else the default banner, else a gradient."""
    for candidate in (background, config.DEFAULT_BACKGROUND):
        if candidate is None:
            continue
        try:
            if isinstance(candidate, bytes):
                source = Image.open(io.BytesIO(candidate))
            elif Path(candidate).is_file():
                source = Image.open(candidate)
            else:
                continue
            with source:
                return ImageOps.fit(source.convert("RGB"), (W, H), RESAMPLE)
        except (OSError, ValueError):
            log.warning("Could not read a background image, trying the next one")
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
def build_welcome_image(avatar_bytes: Optional[bytes], display_name: str, server_name: str,
                        member_count: int, background: Union[Path, bytes, None] = None,
                        alt_name: str = "") -> io.BytesIO:
    """Render the welcome image and return it as an in-memory PNG.

    alt_name (the plain username) is used when the display name has no drawable characters.
    """
    canvas = Image.new("RGBA", (W, H), DARK)
    try:
        # 1. Background banner
        banner = _load_banner(background)
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
        _draw_line(draw, TEXT_X, 165, "WELCOME", True, 30, 30, SOFT_RED, spacing=7)

        name = sanitize_text(display_name)
        if not _draw_line(draw, TEXT_X, 228, name, True, 58, 30, WHITE):
            fallback = sanitize_text(alt_name) or "新成員"
            _draw_line(draw, TEXT_X, 228, fallback, True, 58, 30, WHITE)

        draw.rounded_rectangle((TEXT_X, 270, TEXT_X + 80, 274), radius=2, fill=RED)

        server = sanitize_text(server_name) or "伺服器"
        _draw_line(draw, TEXT_X, 308, f"歡迎加入 {server}", False, 32, 20, SOFT_WHITE)
        _draw_line(draw, TEXT_X, 350, f"第 {member_count:,} 位成員", False, 28, 20, SOFT_RED)

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
    """Validate an uploaded PNG/JPG, pre-fit it to the image size and store it as JPEG.

    Storing the already-cropped result keeps files small and rendering fast.
    """
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG"}:
                raise BackgroundError("只支援 PNG 或 JPG 圖片。")
            if source.width * source.height > config.MAX_BACKGROUND_PIXELS:
                raise BackgroundError("這張圖片的像素太多，請使用約 1200 萬像素以下的圖片。")
            source.draft("RGB", (W * 2, H * 2))  # faster, lower-memory JPEG decode
            fitted = ImageOps.fit(source.convert("RGB"), (W, H), RESAMPLE)
    except BackgroundError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise BackgroundError("無法讀取這個檔案，請確認它是 PNG 或 JPG 圖片。") from exc

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination.with_name(destination.name + ".tmp")
        fitted.save(temp_path, format="JPEG", quality=90, optimize=True)
        temp_path.replace(destination)
    except OSError as exc:
        raise BackgroundError("無法把背景圖儲存到磁碟。") from exc
    finally:
        fitted.close()
