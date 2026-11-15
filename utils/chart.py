"""Line charts for the bot status, drawn with Pillow only (no plotting library, little memory).

Three stacked panels (latency, memory, CPU) in the same minimal line style as the welcome image.
A gap in the data (the bot was offline, or a failover took a minute) breaks the line, and a
dashed vertical line marks the moment another host took over.
"""
import io
from datetime import datetime, timedelta, timezone
from typing import Optional

from PIL import Image, ImageDraw

from utils.image import THEMES, DEFAULT_THEME, _FontSet, _draw_glyphs, _fit_text, _glyphs_width

TAIPEI = timezone(timedelta(hours=8))
W, H = 900, 584
LEFT, RIGHT = 78, W - 32
PANEL_TOP, PANEL_STEP, PLOT_HEIGHT = 92, 156, 112
GAP_SECONDS = 100          # a hole longer than this (3 missed samples) breaks the line


class _Text:
    """Draws text with the bundled fonts, reusing the opened fonts for the whole chart."""

    def __init__(self, draw: ImageDraw.ImageDraw) -> None:
        self.draw, self._sets = draw, {}

    def put(self, x: float, centre_y: float, text: str, size: int, fill, bold: bool = False, align: str = "l",
            max_width: int = 400) -> None:
        key = (bold, size)
        if key not in self._sets:
            self._sets[key] = _FontSet(bold, size)
        fonts = self._sets[key]
        glyphs = fonts.layout(text)
        width = _glyphs_width(glyphs)
        if width > max_width:                                  # rare: very long host names
            fonts, glyphs = _fit_text(bold, text, size, max_width, 10)
            width = _glyphs_width(glyphs)
        left = x - width if align == "r" else x
        _draw_glyphs(self.draw, left, fonts.baseline(centre_y), glyphs, fill)


def _nice_top(value: float) -> float:
    """Round the top of a scale up to 1, 2, 2.5, 5 x 10^n."""
    if value <= 0:
        return 1.0
    magnitude = 1.0
    while value > 10 * magnitude:
        magnitude *= 10
    while value < magnitude:
        magnitude /= 10
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * magnitude:
            return step * magnitude
    return 10 * magnitude


def _fmt(value: float) -> str:
    return f"{value:.0f}" if abs(value) >= 100 else f"{value:.1f}".rstrip("0").rstrip(".") if value % 1 else f"{value:.0f}"


def render_status_chart(samples: list[dict], minutes: int, now: Optional[float] = None,
                        theme: str = DEFAULT_THEME, interval: int = 30) -> bytes:
    """Draw the chart for the last `minutes` minutes and return it as PNG bytes."""
    colours = THEMES.get(theme, THEMES[DEFAULT_THEME])
    bg, fg, muted, line = colours["bg"], colours["fg"], colours["muted"], colours["line"]
    now = now if now is not None else (samples[-1]["ts"] if samples else 0)
    start = now - minutes * 60
    window = [s for s in samples if start <= s["ts"] <= now]

    canvas = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(canvas)
    text = _Text(draw)
    draw.rounded_rectangle((10, 10, W - 11, H - 11), radius=18, outline=line, width=1)

    # ---- header
    host = window[-1]["host"] if window else "-"
    text.put(LEFT - 40, 40, "機器人狀態", 24, fg, bold=True)
    text.put(LEFT - 40, 68, f"{host}　最近 {minutes} 分鐘　每 {interval} 秒記錄一次", 15, muted, max_width=560)
    stamp = datetime.fromtimestamp(now, TAIPEI).strftime("%H:%M:%S") if now else "-"
    text.put(RIGHT, 40, f"更新 {stamp}（UTC+8）", 15, muted, align="r")

    panels = (("延遲", "ms", "latency"), ("記憶體", "MB", "memory"), ("CPU", "%", "cpu"))
    span = max(now - start, 1)
    x_of = lambda ts: LEFT + (ts - start) / span * (RIGHT - LEFT)

    for index, (title, unit, key) in enumerate(panels):
        top = PANEL_TOP + index * PANEL_STEP
        plot_top, plot_bottom = top + 30, top + 30 + PLOT_HEIGHT
        values = [s[key] for s in window if s.get(key) is not None]

        text.put(LEFT - 40, top + 8, title, 17, fg, bold=True)
        text.put(LEFT - 40 + 56, top + 9, unit, 14, muted)
        if values:
            summary = f"現在 {_fmt(values[-1])}　平均 {_fmt(sum(values) / len(values))}　最高 {_fmt(max(values))}"
            text.put(RIGHT, top + 8, summary, 14, muted, align="r")

        high = _nice_top(max(values) * 1.15) if values else 1.0
        y_of = lambda v: plot_bottom - min(max(v / high, 0.0), 1.0) * PLOT_HEIGHT

        for fraction in (0.0, 0.5, 1.0):                                     # hairline grid + labels
            y = plot_bottom - fraction * PLOT_HEIGHT
            draw.line((LEFT, y, RIGHT, y), fill=line, width=1)
            text.put(LEFT - 10, y, _fmt(high * fraction), 13, muted, align="r")

        if index == len(panels) - 1:                                         # time axis under the last panel
            for tick in range(0, minutes + 1, max(1, minutes // 6)):
                ts = start + tick * 60
                x = x_of(ts)
                draw.line((x, plot_bottom, x, plot_bottom + 4), fill=muted, width=1)
                text.put(x, plot_bottom + 16, datetime.fromtimestamp(ts, TAIPEI).strftime("%H:%M"), 13, muted, align="r" if tick == minutes else "l")

        # dashed marker where another host took over
        previous_host = None
        for s in window:
            if previous_host is not None and s["host"] != previous_host:
                x = x_of(s["ts"])
                for y in range(int(plot_top), int(plot_bottom), 8):
                    draw.line((x, y, x, min(y + 4, plot_bottom)), fill=muted, width=1)
                if index == 0:
                    text.put(min(x + 5, RIGHT - 60), plot_top + 9, s["host"], 12, muted, max_width=120)
            previous_host = s["host"]

        # the line itself, broken wherever data is missing
        segment, last_point, last_ts = [], None, 0
        for s in window:
            if s.get(key) is None:
                continue
            point = (x_of(s["ts"]), y_of(s[key]))
            if segment and s["ts"] - last_ts > GAP_SECONDS:
                if len(segment) > 1:
                    draw.line(segment, fill=fg, width=2, joint="curve")
                segment = []
            segment.append(point)
            last_ts, last_point = s["ts"], point
        if len(segment) > 1:
            draw.line(segment, fill=fg, width=2, joint="curve")
        if last_point:
            x, y = last_point
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=bg, outline=fg, width=2)
        if len(values) < 2:
            text.put((LEFT + RIGHT) / 2 - 60, (plot_top + plot_bottom) / 2, "資料還在累積中…", 15, muted)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", compress_level=3)
    canvas.close()
    return buffer.getvalue()
