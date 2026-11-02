"""Welcome image generation with Pillow: minimal line style, four black-and-white themes.

Layout (1200x406, the same ratio as the default banner):
  - A plain background in the theme colour (or a background image, tinted with the theme colour).
  - A thin rounded frame, a circular avatar with two thin rings, and one short accent arc.
  - Text on the left: WELCOME, the display name, a short rule, "welcome to <server>".
  - A thin vertical line, then the member number drawn as outlined digits on the right.

Names from Discord can contain fancy Unicode, emoji or symbols. Text is normalised and every
character is drawn with the first font in assets/fonts that really has a glyph for it, so no
"tofu" boxes appear. Characters that no font supports are dropped instead of being drawn as boxes.

Every image object is closed as soon as it is no longer needed and nothing is cached, so
memory is released right after each render.
"""
import asyncio
import io
import logging
import unicodedata
from pathlib import Path
from typing import Optional, Union

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

import config

log = logging.getLogger(__name__)

RESAMPLE = Image.Resampling.LANCZOS

# One image at a time per process (local requests and requests from the other host
# share this lock), which caps CPU and RAM during bursts.
RENDER_LOCK = asyncio.Semaphore(1)

W, H = config.IMAGE_WIDTH, config.IMAGE_HEIGHT

# --- Themes: a black-and-white family. bg = background, fg = main text and rings,
# --- muted = secondary text, line = hairlines, embed = colour of the Discord embed stripe.
THEMES = {
    "black":    {"label": "黑色", "bg": (10, 10, 11),    "fg": (245, 245, 244), "muted": (145, 148, 154), "line": (58, 60, 66),    "embed": 0xF5F5F4},
    "charcoal": {"label": "深灰", "bg": (31, 33, 37),    "fg": (240, 241, 243), "muted": (155, 160, 168), "line": (74, 78, 86),    "embed": 0xF0F1F3},
    "white":    {"label": "白色", "bg": (247, 247, 245),  "fg": (14, 14, 15),    "muted": (104, 107, 113), "line": (206, 207, 211), "embed": 0x0E0E0F},
    "silver":   {"label": "淺灰", "bg": (227, 229, 232),  "fg": (22, 24, 28),    "muted": (92, 97, 105),   "line": (176, 180, 186), "embed": 0x16181C},
}
DEFAULT_THEME = "black"
BACKGROUND_TINT = 0.80       # how strongly the theme colour covers a background image


def get_theme(name: Optional[str]) -> dict:
    return THEMES.get(name or "", THEMES[DEFAULT_THEME])


def theme_embed_colour(name: Optional[str]) -> int:
    return get_theme(name)["embed"]


# --- Layout constants (pixels at 1x) ----------------------------------------
FRAME_INSET = 20
AVATAR_CENTRE = (186, 203)
AVATAR_RADIUS = 84
TEXT_X = 336
TEXT_MAX_WIDTH = 484                    # the vertical line sits at x = 846
DIVIDER_X = 846
NUMBER_CENTRE_X = (DIVIDER_X + W - FRAME_INSET) // 2     # middle of the right zone
NUMBER_MAX_WIDTH = 270


class BackgroundError(ValueError):
    """Raised when an uploaded background cannot be used."""


# Font families are tried in this order. Any other font file placed in
# assets/fonts is tried afterwards (alphabetical), so adding a font is just
# copying a .ttf/.otf file into that folder.
FONT_PRIORITY = ("NotoSansTC", "NotoSansSC", "NotoSansJP", "NotoSansKR",
                 "NotoSansSymbols", "NotoSansSymbols2", "NotoEmoji")

Glyph = tuple[str, ImageFont.FreeTypeFont]


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


def _circle_avatar(avatar_bytes: Optional[bytes], diameter: int, fallback) -> Image.Image:
    avatar = None
    if avatar_bytes:
        try:
            with Image.open(io.BytesIO(avatar_bytes)) as source:
                avatar = ImageOps.fit(source.convert("RGBA"), (diameter, diameter), RESAMPLE)
        except (OSError, ValueError):
            log.warning("Could not decode avatar image, using a placeholder")
    if avatar is None:
        avatar = Image.new("RGBA", (diameter, diameter), (*fallback, 255))
    mask = _circle_mask(diameter)
    avatar.putalpha(mask)
    mask.close()
    return avatar


def _load_background(background: Union[Path, bytes, None], theme: dict) -> Image.Image:
    """The base layer: a plain theme colour, or a background image covered by the theme colour."""
    plain = Image.new("RGB", (W, H), theme["bg"])
    if background is None:
        return plain
    try:
        if isinstance(background, bytes):
            source = Image.open(io.BytesIO(background))
        elif Path(background).is_file():
            source = Image.open(background)
        else:
            return plain
        with source:
            picture = ImageOps.fit(source.convert("RGB"), (W, H), RESAMPLE)
        grey = ImageOps.grayscale(picture).convert("RGB")      # keep the whole look black and white
        picture.close()
        picture = grey
        tinted = Image.blend(picture, plain, BACKGROUND_TINT)
        picture.close()
        plain.close()
        return tinted
    except (OSError, ValueError):
        log.warning("Could not read a background image, using a plain background")
        return plain


def _line_layer(theme: dict) -> Image.Image:
    """All the thin line work, drawn at 2x for smooth edges and scaled back down."""
    k = 2
    layer = Image.new("RGBA", (W * k, H * k), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    fg, line = (*theme["fg"], 255), (*theme["line"], 255)

    inset = FRAME_INSET * k
    draw.rounded_rectangle((inset, inset, W * k - inset, H * k - inset), radius=22 * k, outline=line, width=3)

    cx, cy = AVATAR_CENTRE[0] * k, AVATAR_CENTRE[1] * k
    inner, outer = (AVATAR_RADIUS + 12) * k, (AVATAR_RADIUS + 30) * k
    draw.ellipse((cx - inner, cy - inner, cx + inner, cy + inner), outline=fg, width=3)
    draw.ellipse((cx - outer, cy - outer, cx + outer, cy + outer), outline=line, width=2)
    draw.arc((cx - outer, cy - outer, cx + outer, cy + outer), start=-52, end=38, fill=fg, width=6)

    draw.line((TEXT_X * k, 216 * k, (TEXT_X + 56) * k, 216 * k), fill=fg, width=6)
    draw.line(((TEXT_X + 66) * k, 216 * k, (DIVIDER_X - 26) * k, 216 * k), fill=line, width=2)
    draw.line((DIVIDER_X * k, 74 * k, DIVIDER_X * k, (H - 74) * k), fill=line, width=2)

    small = layer.resize((W, H), RESAMPLE)
    layer.close()
    return small


def _paste_outlined(canvas: Image.Image, text: str, font: ImageFont.FreeTypeFont,
                    centre: tuple[int, int], colour, stroke: int = 2) -> None:
    """Draw outlined (hollow) text: the stroked glyph minus the plain glyph."""
    left, top, right, bottom = font.getbbox(text, anchor="ls", stroke_width=stroke)
    pad = 4
    width, height = int(right - left) + pad * 2, int(bottom - top) + pad * 2
    origin = (pad - left, pad - top)
    outer = Image.new("L", (width, height), 0)
    inner = Image.new("L", (width, height), 0)
    ImageDraw.Draw(outer).text(origin, text, font=font, fill=255, anchor="ls", stroke_width=stroke)
    ImageDraw.Draw(inner).text(origin, text, font=font, fill=255, anchor="ls")
    ring = ImageChops.subtract(outer, inner)
    x, y = centre[0] - width // 2, centre[1] - height // 2
    canvas.paste(colour, (x, y, x + width, y + height), ring)
    for image in (outer, inner, ring):
        image.close()


# --- Public API ------------------------------------------------------------
def build_welcome_image(avatar_bytes: Optional[bytes], display_name: str, server_name: str,
                        member_count: int, background: Union[Path, bytes, None] = None,
                        alt_name: str = "", theme: str = DEFAULT_THEME) -> io.BytesIO:
    """Render the welcome image and return it as an in-memory PNG.

    alt_name (the plain username) is used when the display name has no drawable characters.
    """
    colours = get_theme(theme)
    fg, muted = colours["fg"], colours["muted"]
    canvas = _load_background(background, colours).convert("RGBA")
    try:
        # 1. Thin line work (frame, rings, rules)
        lines = _line_layer(colours)
        merged = Image.alpha_composite(canvas, lines)
        lines.close()
        canvas.close()
        canvas = merged

        # 2. Avatar inside the rings
        diameter = AVATAR_RADIUS * 2
        avatar = _circle_avatar(avatar_bytes, diameter, colours["line"])
        canvas.paste(avatar, (AVATAR_CENTRE[0] - AVATAR_RADIUS, AVATAR_CENTRE[1] - AVATAR_RADIUS), avatar)
        avatar.close()

        draw = ImageDraw.Draw(canvas)

        # 3. Text block
        _draw_line(draw, TEXT_X, 118, "WELCOME", True, 20, 20, muted, spacing=9)

        name = sanitize_text(display_name)
        if not _draw_line(draw, TEXT_X, 176, name, True, 60, 30, fg):
            _draw_line(draw, TEXT_X, 176, sanitize_text(alt_name) or "新成員", True, 60, 30, fg)

        server = sanitize_text(server_name) or "伺服器"
        _draw_line(draw, TEXT_X, 262, f"歡迎加入 {server}", False, 28, 18, fg)

        # 4. Member number: small label and outlined digits on the right
        _draw_line(draw, NUMBER_CENTRE_X - 38, 150, "MEMBER", True, 16, 16, muted, spacing=7)
        digits = f"{member_count:,}"
        number_font = _fit_text(True, digits, 112, NUMBER_MAX_WIDTH, 36)[0].primary
        _paste_outlined(canvas, digits, number_font, (NUMBER_CENTRE_X, 222), muted, stroke=2)

        # 5. Encode, then release the canvas immediately
        rgb = canvas.convert("RGB")
        buffer = io.BytesIO()
        rgb.save(buffer, format="PNG", compress_level=6)
        rgb.close()
        buffer.seek(0)
        return buffer
    finally:
        canvas.close()


def process_background(data: bytes) -> bytes:
    """Validate an uploaded PNG/JPG, fit it to the image size and return it as a small JPEG.

    Only this small result is stored (in the database), never the original upload.
    """
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG"}:
                raise BackgroundError("只支援 PNG 或 JPG 圖片。")
            limit = config.MAX_JPEG_PIXELS if source.format == "JPEG" else config.MAX_BACKGROUND_PIXELS
            if source.width * source.height > limit:
                raise BackgroundError(f"這張圖片的像素太多（上限約 {limit // 1_000_000} 百萬像素），請縮小後再上傳。")
            source.draft("RGB", (W, H))  # JPEG: decode at a reduced size that is still big enough
            rgb = source if source.mode == "RGB" else source.convert("RGB")   # no extra copy when already RGB
            fitted = ImageOps.fit(rgb, (W, H), RESAMPLE)
            if rgb is not source:
                rgb.close()
    except BackgroundError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise BackgroundError("無法讀取這個檔案，請確認它是 PNG 或 JPG 圖片。") from exc

    try:
        result = b""
        for quality in (90, 80, 70, 60):
            buffer = io.BytesIO()
            fitted.save(buffer, format="JPEG", quality=quality, optimize=True)
            result = buffer.getvalue()
            if len(result) <= config.MAX_STORED_BACKGROUND_BYTES:
                break
        return result
    finally:
        fitted.close()


# --- Loading thumbnail (transparent GIF) -------------------------------------
ALPHA_CUTOFF = 64          # GIF only knows "transparent" or "opaque"; soft edges are cut here
KEY_CUTOFF = 28            # brightness below this counts as background when keying out black
ROTATE_FRAMES = 24
FRAME_MS = 80


def _key_out_black(frame: Image.Image) -> Image.Image:
    """Opaque image on a dark background -> same image with the dark background transparent.

    Opacity follows brightness (the brightest channel), so soft glows fade out smoothly.
    """
    rgba = frame.convert("RGBA")
    red, green, blue, alpha = rgba.split()
    brightness = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    brightness = brightness.point(lambda v: 0 if v < KEY_CUTOFF else min(255, int(v * 1.35)))
    bright_rgb = Image.merge("RGB", [band.point(lambda v: min(255, int(v * 1.8))) for band in (red, green, blue)])
    bright_rgb.putalpha(ImageChops.darker(alpha, brightness))
    for image in (red, green, blue, alpha, brightness, rgba):
        image.close()
    return bright_rgb


def _gif_frames(frames: list[Image.Image], size: int, durations: list[int]) -> bytes:
    """RGBA frames -> one GIF with a transparent background (shared palette, no dithering)."""
    flat = []
    for frame in frames:
        small = ImageOps.contain(frame, (size, size), RESAMPLE)
        flat.append(small)
    # One palette for every frame, taken from a mosaic of all of them (so later frames keep their colours).
    cols = max(1, int(len(flat) ** 0.5 + 0.999))
    tile = min(size, max(48, 480 // cols))
    mosaic = Image.new("RGB", (tile * cols, tile * (-(-len(flat) // cols))))
    for number, small in enumerate(flat):
        mosaic.paste(small.convert("RGB").resize((tile, tile), RESAMPLE), ((number % cols) * tile, (number // cols) * tile))
    palette_source = mosaic.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    mosaic.close()
    indexed = []
    for small in flat:
        frame_p = small.convert("RGB").quantize(palette=palette_source, dither=Image.Dither.NONE)
        hole = small.getchannel("A").point(lambda v: 255 if v < ALPHA_CUTOFF else 0)
        frame_p.paste(255, mask=hole)          # palette index 255 = transparent
        indexed.append(frame_p)
        hole.close()
    buffer = io.BytesIO()
    indexed[0].save(buffer, format="GIF", save_all=True, append_images=indexed[1:], duration=durations,
                    loop=0, transparency=255, disposal=2, optimize=False)
    for image in flat + indexed + [palette_source]:
        image.close()
    return buffer.getvalue()


def process_loading(data: bytes, *, transparent: bool = True, rotate: bool = False) -> bytes:
    """Turn an uploaded picture (PNG, JPG, WebP or GIF, still or animated) into a small GIF.

    transparent: make a dark background see-through (images that already have transparency keep it).
    rotate: for a single picture, add a slow full rotation so it looks like "loading".
    """
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                raise BackgroundError("只支援 PNG、JPG、WebP 或 GIF 圖片。")
            if source.width * source.height > config.MAX_BACKGROUND_PIXELS:
                raise BackgroundError("這張圖片的像素太多，請縮小後再上傳。")
            total = getattr(source, "n_frames", 1)
            step = max(1, -(-total // config.LOADING_MAX_FRAMES))        # keep at most 40 frames
            frames, durations = [], []
            for index in range(0, total, step):
                source.seek(index)
                raw = source.convert("RGBA")
                has_alpha = raw.getchannel("A").getextrema()[0] < 255
                frames.append(_key_out_black(raw) if transparent and not has_alpha else raw)
                durations.append(int(source.info.get("duration") or FRAME_MS) * step)
                if frames[-1] is not raw:
                    raw.close()
    except BackgroundError:
        raise
    except (OSError, ValueError, EOFError, Image.DecompressionBombError) as exc:
        raise BackgroundError("無法讀取這個檔案，請確認它是 PNG、JPG、WebP 或 GIF 圖片。") from exc

    try:
        if len(frames) == 1 and rotate:
            base = frames[0]
            turned = []
            for n in range(ROTATE_FRAMES):
                turned.append(base.rotate(-360 * n / ROTATE_FRAMES, resample=Image.Resampling.BICUBIC))
            frames, durations = turned, [FRAME_MS] * ROTATE_FRAMES
            base.close()
        for size in (config.LOADING_SIZE, 200, 160, 128):                  # shrink until it is small enough
            result = _gif_frames(frames, size, durations)
            if len(result) <= config.LOADING_MAX_BYTES:
                return result
        raise BackgroundError("這張動畫太大，請換一張較小或較短的。")
    finally:
        for frame in frames:
            frame.close()
