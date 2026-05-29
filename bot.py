# ============================================================
# IDR WATCH - SMART CARD IMAGE GENERATOR
# ============================================================
import os
import json
import time
import re
import io
import feedparser
import requests
from datetime import datetime
from urllib.parse import unquote
import pytz
from PIL import Image, ImageDraw, ImageFont
from google import genai
import random
import math

KEYWORDS = [
    "rupiah", "USD/IDR", "BI rate", "Bank Indonesia", "inflasi Indonesia", "IHSG",
    "Fed rate", "FOMC", "dolar AS", "yield Treasury",
    "bitcoin", "ethereum", "kripto",
    "China economy", "ekonomi China", "yuan",
    "emas", "minyak", "nikel"
]

BLACKLIST = ["pilkada", "gosip", "artis", "sinetron", "resep", "olahraga"]
MAX_ARTICLES_PER_RUN = 1
MAX_STORED_URLS = 100
FOOTER = '\n\n— <a href="https://t.me/idrwatch">IDR Watch 🇮🇩</a>'
ON_API_FAIL = "skip"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
POSTED_FILE = "posted.json"
WIB = pytz.timezone("Asia/Jakarta")

GEMINI_KEYS = [
    os.environ["GEMINI_API_KEY_1"],
    os.environ["GEMINI_API_KEY_2"],
    os.environ["GEMINI_API_KEY_3"],
]

# ============================================================
# FONT LOADER
# ============================================================

FONT_PATHS = {
    "bold":    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "mono":    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
}

def load_font(style="bold", size=48):
    try:
        return ImageFont.truetype(FONT_PATHS.get(style, FONT_PATHS["bold"]), size)
    except:
        return ImageFont.load_default()

# ============================================================
# CONTEXT DETECTOR
# ============================================================

def detect_context(title, keyword):
    """Return (sentiment, topic, accent_color, bg_colors, icon)"""
    t = (title + keyword).lower()

    # --- Sentiment ---
    bullish_words = ["naik", "menguat", "positif", "surplus", "tumbuh", "rebound", "rally", "record"]
    bearish_words = ["turun", "melemah", "negatif", "defisit", "anjlok", "crash", "resesi", "jatuh"]

    sentiment = "neutral"
    for w in bullish_words:
        if w in t:
            sentiment = "bullish"
            break
    for w in bearish_words:
        if w in t:
            sentiment = "bearish"
            break

    # --- Topic + icon ---
    topic_map = [
        (["bitcoin", "ethereum", "kripto", "btc", "eth"],        "crypto",   "₿"),
        (["emas", "gold"],                                         "gold",     "◈"),
        (["minyak", "oil", "crude"],                              "oil",      "⬡"),
        (["ihsg", "saham", "bursa", "idx"],                       "equity",   "▲"),
        (["rupiah", "usd/idr", "dolar", "kurs"],                  "fx",       "◎"),
        (["bi rate", "bank indonesia", "suku bunga"],             "central",  "▣"),
        (["inflasi", "cpi", "deflasi"],                           "macro",    "↕"),
        (["fed", "fomc", "treasury", "yield"],                    "fed",      "★"),
        (["china", "yuan", "cny"],                                "china",    "◆"),
        (["nikel", "batu bara", "komoditas", "commodity"],        "commodity","◇"),
    ]
    topic, icon = "general", "◉"
    for keywords_list, tpc, icn in topic_map:
        if any(k in t for k in keywords_list):
            topic, icon = tpc, icn
            break

    # --- Color palette per topic + sentiment override ---
    palettes = {
        "crypto":    {"bg": [(15, 10, 35), (40, 20, 80)],    "accent": (138, 92, 255)},
        "gold":      {"bg": [(30, 22, 8),  (70, 50, 10)],    "accent": (255, 200, 50)},
        "oil":       {"bg": [(20, 18, 15), (50, 40, 25)],    "accent": (255, 140, 30)},
        "equity":    {"bg": [(8, 25, 18),  (15, 55, 35)],    "accent": (50, 230, 140)},
        "fx":        {"bg": [(10, 20, 40), (20, 45, 80)],    "accent": (60, 160, 255)},
        "central":   {"bg": [(20, 20, 30), (35, 35, 60)],    "accent": (180, 180, 255)},
        "macro":     {"bg": [(25, 15, 25), (55, 30, 55)],    "accent": (210, 100, 220)},
        "fed":       {"bg": [(5, 15, 5),   (10, 40, 10)],    "accent": (80, 200, 80)},
        "china":     {"bg": [(35, 5, 5),   (80, 15, 15)],    "accent": (255, 60, 60)},
        "commodity": {"bg": [(20, 20, 15), (50, 48, 30)],    "accent": (200, 190, 80)},
        "general":   {"bg": [(15, 15, 25), (35, 35, 55)],    "accent": (100, 180, 255)},
    }

    palette = palettes.get(topic, palettes["general"])

    # Tint accent berdasarkan sentiment
    r, g, b = palette["accent"]
    if sentiment == "bullish":
        accent = (min(r, 80), max(g, 200), min(b, 120))
    elif sentiment == "bearish":
        accent = (max(r, 220), min(g, 80), min(b, 80))
    else:
        accent = palette["accent"]

    return sentiment, topic, accent, palette["bg"], icon

# ============================================================
# LAYOUT STYLES
# ============================================================

def draw_gradient_bg(pixels, w, h, c1, c2):
    """Diagonal gradient dari kiri-atas ke kanan-bawah"""
    for y in range(h):
        for x in range(w):
            t = (x / w * 0.4 + y / h * 0.6)
            r = int(c1[0] + (c2[0] - c1[0]) * t)
            g = int(c1[1] + (c2[1] - c1[1]) * t)
            b = int(c1[2] + (c2[2] - c1[2]) * t)
            pixels[x, y] = (r, g, b)

def draw_noise_overlay(draw, w, h, alpha=18):
    """Subtle noise texture biar gak flat"""
    rng = random.Random(42)
    for _ in range(w * h // 8):
        x = rng.randint(0, w - 1)
        y = rng.randint(0, h - 1)
        v = rng.randint(0, 255)
        draw.point((x, y), fill=(v, v, v))

def draw_grid_lines(draw, w, h, color):
    """Subtle grid — cocok buat finance/chart vibe"""
    r, g, b = color
    line_color = (min(r + 20, 255), min(g + 20, 255), min(b + 20, 255))
    for x in range(0, w, 80):
        draw.line([(x, 0), (x, h)], fill=(*line_color, 15), width=1)
    for y in range(0, h, 80):
        draw.line([(0, y), (w, y)], fill=(*line_color, 15), width=1)

def draw_diagonal_stripes(draw, w, h, accent):
    """Diagonal accent stripes di corner"""
    r, g, b = accent
    stripe_color = (r, g, b, 20)
    for i in range(-h, w + h, 60):
        draw.line([(i, 0), (i + h, h)], fill=stripe_color, width=25)

def draw_circle_accent(draw, w, h, accent, pos="br"):
    """Large blurred circle sebagai accent element"""
    r, g, b = accent
    if pos == "br":
        cx, cy = w - 80, h - 60
    elif pos == "tl":
        cx, cy = 80, 60
    else:
        cx, cy = w // 2, h // 2

    # Simulate blur dengan multiple circles menurun opacity
    for radius, opacity in [(300, 8), (220, 12), (150, 18), (90, 22), (50, 28)]:
        bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
        draw.ellipse(bbox, fill=(r, g, b, opacity))

def draw_corner_bracket(draw, w, h, accent, thickness=3):
    """Corner brackets — editorial style"""
    size = 50
    r, g, b = accent
    color = (r, g, b, 200)
    pad = 24

    # Top-left
    draw.rectangle([pad, pad, pad + size, pad + thickness], fill=color)
    draw.rectangle([pad, pad, pad + thickness, pad + size], fill=color)

    # Top-right
    draw.rectangle([w - pad - size, pad, w - pad, pad + thickness], fill=color)
    draw.rectangle([w - pad - thickness, pad, w - pad, pad + size], fill=color)

    # Bottom-left
    draw.rectangle([pad, h - pad - thickness, pad + size, h - pad], fill=color)
    draw.rectangle([pad, h - pad - size, pad + thickness, h - pad], fill=color)

    # Bottom-right
    draw.rectangle([w - pad - size, h - pad - thickness, w - pad, h - pad], fill=color)
    draw.rectangle([w - pad - thickness, h - pad - size, w - pad, h - pad], fill=color)

def draw_bar_chart_deco(draw, w, h, accent, sentiment):
    """Mini fake bar chart di background — cocok finance"""
    r, g, b = accent
    bar_w = 28
    gap = 14
    n_bars = 10
    max_bh = 140
    base_y = h - 90
    start_x = w - (bar_w + gap) * n_bars - 40

    rng = random.Random(77)
    for i in range(n_bars):
        bh = rng.randint(30, max_bh)
        x0 = start_x + i * (bar_w + gap)
        x1 = x0 + bar_w
        y0 = base_y - bh
        y1 = base_y

        # Last bar = highlight
        if i == n_bars - 1:
            color = (r, g, b, 160)
        else:
            color = (r, g, b, 55)

        draw.rectangle([x0, y0, x1, y1], fill=color)

def draw_sine_wave(draw, w, h, accent):
    """Subtle sine wave line di background"""
    r, g, b = accent
    points = []
    for x in range(0, w + 1, 4):
        y = h // 2 + int(60 * math.sin(x / 80))
        points.append((x, y))

    for i in range(len(points) - 1):
        draw.line([points[i], points[i + 1]], fill=(r, g, b, 30), width=2)

def draw_hexagon_pattern(draw, w, h, accent):
    """Honeycomb pattern di background sudut"""
    r, g, b = accent
    size = 35
    for row in range(8):
        for col in range(12):
            cx = col * size * 1.8 + (size if row % 2 else 0) - 100
            cy = row * size * 1.55 - 80
            pts = [
                (cx + size * math.cos(math.radians(60 * i - 30)),
                 cy + size * math.sin(math.radians(60 * i - 30)))
                for i in range(6)
            ]
            draw.polygon(pts, outline=(r, g, b, 22), fill=None)

def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = []
    for word in words:
        current.append(word)
        bbox = draw.textbbox((0, 0), " ".join(current), font=font)
        if bbox[2] - bbox[0] > max_width:
            if len(current) > 1:
                lines.append(" ".join(current[:-1]))
                current = [word]
            else:
                lines.append(word)
                current = []
    if current:
        lines.append(" ".join(current))
    return lines[:3]

def draw_text_shadow(draw, pos, text, font, color, shadow_offset=3, shadow_alpha=60):
    r, g, b = color
    sx, sy = pos[0] + shadow_offset, pos[1] + shadow_offset
    draw.text((sx, sy), text, font=font, fill=(0, 0, 0))
    draw.text(pos, text, font=font, fill=color)

# ============================================================
# STYLE VARIANTS
# ============================================================

STYLE_POOL = ["editorial", "terminal", "minimal", "bold_type", "dashboard"]

def pick_style(topic, sentiment):
    """
    Topic + sentiment → style yang cocok.
    Tapi masih ada randomness biar gak monoton.
    """
    preferred = {
        "crypto":    ["terminal", "bold_type"],
        "gold":      ["editorial", "minimal"],
        "oil":       ["dashboard", "bold_type"],
        "equity":    ["dashboard", "editorial"],
        "fx":        ["terminal", "dashboard"],
        "central":   ["minimal", "editorial"],
        "macro":     ["editorial", "minimal"],
        "fed":       ["terminal", "bold_type"],
        "china":     ["bold_type", "editorial"],
        "commodity": ["dashboard", "minimal"],
        "general":   STYLE_POOL,
    }
    pool = preferred.get(topic, STYLE_POOL)
    return random.choice(pool)

def style_editorial(img, draw, w, h, title, topic, accent, bg_colors, icon, sentiment, watermark):
    """Magazine editorial — clean, bracket corners, big icon left, title right"""
    # Background
    pixels = img.load()
    draw_gradient_bg(pixels, w, h, bg_colors[0], bg_colors[1])

    # Noise
    draw_noise_overlay(draw, w, h)

    # Hexagon pattern faint
    draw_hexagon_pattern(draw, w, h, accent)

    # Corner brackets
    draw_corner_bracket(draw, w, h, accent)

    # Big icon left panel
    r, g, b = accent
    panel_w = 340
    draw.rectangle([0, 0, panel_w, h], fill=(*bg_colors[0], 255))
    draw.line([(panel_w, 0), (panel_w, h)], fill=(*accent, 120), width=2)

    icon_font = load_font("bold", 140)
    ib = draw.textbbox((0, 0), icon, font=icon_font)
    ix = (panel_w - (ib[2] - ib[0])) // 2
    iy = (h - (ib[3] - ib[1])) // 2
    draw.text((ix + 3, iy + 3), icon, font=icon_font, fill=(0, 0, 0))
    draw.text((ix, iy), icon, font=icon_font, fill=accent)

    # Topic label
    label_font = load_font("regular", 22)
    topic_label = topic.upper()
    draw.text((panel_w + 36, 50), topic_label, font=label_font, fill=(*accent, 200))

    # Accent line
    draw.rectangle([panel_w + 36, 82, panel_w + 36 + 60, 85], fill=accent)

    # Title
    title_font = load_font("bold", 54)
    lines = wrap_text(draw, title, title_font, w - panel_w - 72)
    ty = 110
    for line in lines:
        draw_text_shadow(draw, (panel_w + 36, ty), line, title_font, (240, 240, 240))
        ty += 70

    # Sentiment tag
    sentiment_labels = {"bullish": "▲ POSITIF", "bearish": "▼ NEGATIF", "neutral": "● NETRAL"}
    sent_text = sentiment_labels.get(sentiment, "● NETRAL")
    sent_font = load_font("bold", 22)
    draw.text((panel_w + 36, h - 80), sent_text, font=sent_font, fill=accent)

    # Watermark
    wm_font = load_font("regular", 22)
    wb = draw.textbbox((0, 0), watermark, font=wm_font)
    draw.text((w - (wb[2] - wb[0]) - 30, h - 50), watermark, font=wm_font, fill=(*accent, 160))


def style_terminal(img, draw, w, h, title, topic, accent, bg_colors, icon, sentiment, watermark):
    """Terminal/hacker vibe — dark, monospace, scanlines, code aesthetic"""
    pixels = img.load()

    # Very dark bg
    dark1 = (5, 12, 5)
    dark2 = (10, 22, 10)
    if topic in ["crypto", "fed"]:
        dark1, dark2 = (5, 5, 18), (8, 8, 35)
    elif topic == "china":
        dark1, dark2 = (18, 5, 5), (35, 8, 8)

    draw_gradient_bg(pixels, w, h, dark1, dark2)

    # Scanlines
    r, g, b = accent
    for y in range(0, h, 4):
        draw.line([(0, y), (w, y)], fill=(r, g, b, 6), width=1)

    # Grid
    draw_grid_lines(draw, w, h, dark2)

    # Top bar
    draw.rectangle([0, 0, w, 55], fill=(*accent, 220))
    bar_font = load_font("mono", 24)
    bar_text = f"  ◉ IDR-WATCH  //  {topic.upper()}  //  BREAKING"
    draw.text((10, 14), bar_text, font=bar_font, fill=(0, 0, 0))

    # Prompt-style prefix
    prompt_font = load_font("mono", 28)
    draw.text((40, 90), "$ ./alert --topic", font=prompt_font, fill=(*accent, 160))

    # Icon
    icon_font = load_font("mono", 100)
    ib = draw.textbbox((0, 0), icon, font=icon_font)
    draw.text((w - (ib[2] - ib[0]) - 50, 80), icon, font=icon_font, fill=(*accent, 50))

    # Title
    title_font = load_font("mono", 46)
    lines = wrap_text(draw, title, title_font, w - 100)
    ty = 150
    for line in lines:
        draw.text((40, ty), line, font=title_font, fill=(220, 220, 220))
        ty += 62

    # Blinking cursor sim
    draw.rectangle([42, ty + 8, 42 + 22, ty + 38], fill=(*accent, 200))

    # Bottom status
    status_labels = {"bullish": "STATUS: BULLISH ▲", "bearish": "STATUS: BEARISH ▼", "neutral": "STATUS: WATCH ●"}
    status_font = load_font("mono", 22)
    draw.text((40, h - 55), status_labels.get(sentiment, "STATUS: WATCH ●"), font=status_font, fill=(*accent, 200))

    # Watermark
    wb = draw.textbbox((0, 0), watermark, font=status_font)
    draw.text((w - (wb[2] - wb[0]) - 30, h - 55), watermark, font=status_font, fill=(*accent, 140))


def style_minimal(img, draw, w, h, title, topic, accent, bg_colors, icon, sentiment, watermark):
    """Clean minimal — lotsa whitespace, single strong accent line, refined"""
    pixels = img.load()

    # Near-white or near-black based on sentiment
    if sentiment == "bearish":
        base = (12, 12, 15)
        text_color = (230, 230, 230)
    else:
        base = (245, 244, 240)
        text_color = (20, 20, 25)

    for y in range(h):
        for x in range(w):
            pixels[x, y] = base

    # Thick left accent bar
    r, g, b = accent
    draw.rectangle([0, 0, 12, h], fill=accent)

    # Large background icon (ghost)
    ghost_font = load_font("bold", 280)
    gb = draw.textbbox((0, 0), icon, font=ghost_font)
    gx = w - (gb[2] - gb[0]) - 20
    gy = (h - (gb[3] - gb[1])) // 2 - 20
    draw.text((gx, gy), icon, font=ghost_font, fill=(*accent, 18))

    # Topic chip
    chip_font = load_font("bold", 20)
    chip_text = f"  {topic.upper()}  "
    cb = draw.textbbox((0, 0), chip_text, font=chip_font)
    draw.rectangle([40, 48, 40 + (cb[2] - cb[0]) + 4, 48 + (cb[3] - cb[1]) + 6], fill=accent)
    draw.text((42, 50), chip_text, font=chip_font, fill=(0, 0, 0))

    # Title
    title_font = load_font("bold", 58)
    lines = wrap_text(draw, title, title_font, w - 120)
    ty = 120
    for line in lines:
        draw.text((40, ty), line, font=title_font, fill=text_color)
        ty += 76

    # Thin divider line
    draw.rectangle([40, ty + 10, 300, ty + 13], fill=(*accent, 200))

    # Sentiment
    sent_labels = {"bullish": "▲ Positif", "bearish": "▼ Negatif", "neutral": "● Netral"}
    sent_font = load_font("regular", 24)
    draw.text((40, ty + 28), sent_labels.get(sentiment, "● Netral"), font=sent_font, fill=(*accent, 220))

    # Watermark
    wm_font = load_font("regular", 20)
    wb = draw.textbbox((0, 0), watermark, font=wm_font)
    draw.text((w - (wb[2] - wb[0]) - 30, h - 44), watermark, font=wm_font, fill=(*accent, 160))


def style_bold_type(img, draw, w, h, title, topic, accent, bg_colors, icon, sentiment, watermark):
    """Bold typography — giant text, kontras tinggi, stripe accent"""
    pixels = img.load()

    # Solid dark bg
    dark = (10, 10, 12)
    for y in range(h):
        for x in range(w):
            pixels[x, y] = dark

    r, g, b = accent

    # Diagonal stripes (very faint)
    draw_diagonal_stripes(draw, w, h, accent)

    # Big accent block top
    draw.rectangle([0, 0, w, 18], fill=accent)

    # Giant icon background watermark
    huge_font = load_font("bold", 320)
    hb = draw.textbbox((0, 0), icon, font=huge_font)
    hx = (w - (hb[2] - hb[0])) // 2
    hy = (h - (hb[3] - hb[1])) // 2
    draw.text((hx, hy), icon, font=huge_font, fill=(r, g, b, 25))

    # Accent bottom block
    draw.rectangle([0, h - 18, w, h], fill=accent)

    # Topic
    topic_font = load_font("bold", 24)
    draw.text((36, 36), f"— {topic.upper()} —", font=topic_font, fill=accent)

    # Title — very large, bold
    title_font = load_font("bold", 62)
    lines = wrap_text(draw, title, title_font, w - 80)
    ty = 100
    for line in lines:
        draw_text_shadow(draw, (36, ty), line, title_font, (245, 245, 245))
        ty += 80

    # Sentiment pill
    sent_map = {"bullish": ("▲ NAIK", (30, 200, 80)), "bearish": ("▼ TURUN", (220, 50, 50)), "neutral": ("● WATCH", accent)}
    sent_text, sent_color = sent_map.get(sentiment, ("● WATCH", accent))
    pill_font = load_font("bold", 26)
    pb = draw.textbbox((0, 0), f" {sent_text} ", font=pill_font)
    pw = pb[2] - pb[0] + 20
    ph = pb[3] - pb[1] + 12
    draw.rounded_rectangle([36, h - 65 - ph, 36 + pw, h - 65], radius=6, fill=sent_color)
    draw.text((46, h - 60 - (ph - 12) // 2 - 8), f" {sent_text} ", font=pill_font, fill=(255, 255, 255))

    # Watermark
    wm_font = load_font("regular", 22)
    wb = draw.textbbox((0, 0), watermark, font=wm_font)
    draw.text((w - (wb[2] - wb[0]) - 30, h - 55), watermark, font=wm_font, fill=(*accent, 150))


def style_dashboard(img, draw, w, h, title, topic, accent, bg_colors, icon, sentiment, watermark):
    """Dashboard / data panel — bar chart deco, top ticker bar, metric style"""
    pixels = img.load()

    draw_gradient_bg(pixels, w, h, bg_colors[0], bg_colors[1])
    draw_noise_overlay(draw, w, h)

    r, g, b = accent

    # Sine wave deco
    draw_sine_wave(draw, w, h, accent)

    # Bar chart background deco
    draw_bar_chart_deco(draw, w, h, accent, sentiment)

    # Top ticker bar
    draw.rectangle([0, 0, w, 52], fill=(*bg_colors[0], 230))
    draw.line([(0, 52), (w, 52)], fill=(*accent, 180), width=1)

    ticker_font = load_font("mono", 20)
    ticker = f"  IDR WATCH  ·  {topic.upper()}  ·  LIVE UPDATE  ·  {'↑' if sentiment == 'bullish' else '↓' if sentiment == 'bearish' else '→'}  "
    draw.text((10, 14), ticker, font=ticker_font, fill=(*accent, 220))

    # Icon (left side, medium)
    icon_font = load_font("bold", 90)
    ib = draw.textbbox((0, 0), icon, font=icon_font)
    draw.text((44, 80), icon, font=icon_font, fill=(*accent, 180))

    left_margin = 50 + (ib[2] - ib[0]) + 24

    # Metric label
    label_font = load_font("regular", 22)
    draw.text((left_margin, 80), topic.upper(), font=label_font, fill=(*accent, 180))
    draw.rectangle([left_margin, 108, left_margin + 45, 111], fill=accent)

    # Title
    title_font = load_font("bold", 50)
    lines = wrap_text(draw, title, title_font, w - left_margin - 50)
    ty = 118
    for line in lines:
        draw_text_shadow(draw, (left_margin, ty), line, title_font, (235, 235, 235))
        ty += 66

    # Bottom info bar
    draw.rectangle([0, h - 60, w, h], fill=(*bg_colors[0], 200))
    draw.line([(0, h - 60), (w, h - 60)], fill=(*accent, 100), width=1)

    sent_labels = {"bullish": "▲ Bullish", "bearish": "▼ Bearish", "neutral": "● Neutral"}
    info_font = load_font("bold", 22)
    draw.text((30, h - 43), sent_labels.get(sentiment, "● Neutral"), font=info_font, fill=accent)

    wm_font = load_font("regular", 20)
    wb = draw.textbbox((0, 0), watermark, font=wm_font)
    draw.text((w - (wb[2] - wb[0]) - 24, h - 42), watermark, font=wm_font, fill=(*accent, 160))


# ============================================================
# MAIN IMAGE GENERATOR
# ============================================================

def generate_card_image(title, keyword="", watermark="@idrwatch"):
    """
    Generate stylish card image berdasarkan context.
    No external API, no scraping. Pure local generation.
    """
    try:
        W, H = 1200, 630
        sentiment, topic, accent, bg_colors, icon = detect_context(title, keyword)
        style = pick_style(topic, sentiment)

        print(f"      ✎ Style: {style} | Topic: {topic} | Sentiment: {sentiment} | Icon: {icon}")

        img = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img, "RGBA")

        style_fn = {
            "editorial":  style_editorial,
            "terminal":   style_terminal,
            "minimal":    style_minimal,
            "bold_type":  style_bold_type,
            "dashboard":  style_dashboard,
        }[style]

        style_fn(img, draw, W, H, title, topic, accent, bg_colors, icon, sentiment, watermark)

        img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        buf.seek(0)

        print(f"      ✓ Card generated ({style})")
        return buf

    except Exception as e:
        print(f"      ✗ Card generation error: {e}")
        return None


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def load_posted():
    try:
        with open(POSTED_FILE) as f:
            return json.load(f)
    except:
        return {"posted": [], "last_updated": ""}

def save_posted(data):
    data["posted"] = data["posted"][-MAX_STORED_URLS:]
    data["last_updated"] = datetime.now(WIB).isoformat()
    with open(POSTED_FILE, "w") as f:
        json.dump(data, f, indent=2)

def clean_title(title):
    return re.sub(r'\s[-|]\s.*$', '', title).strip()

def extract_real_url_from_google_news(google_news_url):
    try:
        match = re.search(r'[?&]q=([^&]+)', google_news_url)
        if match:
            real_url = unquote(match.group(1))
            if real_url.startswith('http'):
                return real_url
    except:
        pass
    return google_news_url

def fetch_articles():
    articles = []
    seen_titles = set()
    for keyword in KEYWORDS:
        url = (
            f"https://news.google.com/rss/search"
            f"?q={requests.utils.quote(keyword)}+when:6h"
            f"&hl=id&gl=ID&ceid=ID:id"
        )
        try:
            feed = feedparser.parse(url)
            if feed.bozo:
                continue
            for entry in feed.entries[:3]:
                title = clean_title(entry.title)
                if title.lower() in seen_titles:
                    continue
                seen_titles.add(title.lower())
                real_url = extract_real_url_from_google_news(entry.link)
                articles.append({
                    "title": title,
                    "url": real_url,
                    "keyword": keyword,
                    "summary": re.sub(r'<[^>]+>', '', getattr(entry, "summary", "")),
                })
        except:
            continue
    return articles

def is_blacklisted(title):
    return any(bl.lower() in title.lower() for bl in BLACKLIST)

_gemini_key_index = 0

def get_gemini_client():
    global _gemini_key_index
    return genai.Client(api_key=GEMINI_KEYS[_gemini_key_index % len(GEMINI_KEYS)])

def gemini(prompt):
    global _gemini_key_index
    for _ in range(len(GEMINI_KEYS)):
        try:
            client = get_gemini_client()
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt
            )
            return response.text.strip()
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                print(f"      Key {_gemini_key_index + 1} rate limited...")
                _gemini_key_index += 1
                continue
            raise e
    return None

def is_relevant(title, summary):
    prompt = f"""
Kamu kurator channel ekonomi Indonesia. Tentukan apakah berita ini cukup penting dan relevan untuk dipost ke channel.
Konteks berita: {summary}
Kriteria LAYAK:
- Berdampak langsung ke ekonomi Indonesia atau masyarakat umum
- Ada angka/data signifikan (kurs, inflasi, suku bunga, dll)
- Keterkaitan global yang dampaknya nyata ke Indonesia
Kriteria TIDAK LAYAK:
- Berita daerah terlalu lokal dan tidak berdampak nasional
- Prediksi/opini tanpa data kuat
- Berita seremonial/rapat tanpa output jelas
Jawab hanya dengan: YA atau TIDAK
"""
    try:
        result = gemini(prompt)
        return result.upper().startswith("YA")
    except:
        return False

def generate_narasi(title, summary):
    prompt = f"""
Kamu admin channel Telegram ekonomi Indonesia. Gaya nulis: singkat, padat, langsung to the point.
Kayak Watcher.Guru tapi versi lokal.
Berita: {title}
Konteks: {summary}
Format output WAJIB:
[1 kalimat inti berita]
[1 kalimat dampak/konteks global kalau ada]
[1 kalimat "artinya buat lo" — singkat, no bullshit]
Aturan keras:
- Maksimal 3 kalimat, NO LEBIH
- Tidak ada basa-basi, langsung inti
- Tidak ada kata "guys", "nih", "yuk", "deh", "banget"
- Tidak ada kalimat pembuka seperti "Jadi", "Nah", "Eh"
- Kalau ga ada dampak global yang relevan, skip baris kedua
- Bahasa Indonesia tapi boleh campur 1-2 kata Inggris yang udah umum
"""
    try:
        return gemini(prompt)
    except:
        return None

def send_telegram(msg, image_buf=None):
    if image_buf:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        try:
            requests.post(url, data={
                "chat_id": TELEGRAM_CHANNEL_ID,
                "caption": msg,
                "parse_mode": "HTML"
            }, files={
                "photo": ("image.jpg", image_buf, "image/jpeg")
            }, timeout=15)
            print(f"    ✓ Telegram photo sent")
            return True
        except Exception as e:
            print(f"    ✗ Telegram photo error: {e}")
            return False
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            requests.post(url, json={
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": msg,
                "parse_mode": "HTML"
            }, timeout=10)
            print(f"    ✓ Telegram text sent")
            return True
        except Exception as e:
            print(f"    ✗ Telegram error: {e}")
            return False


# ============================================================
# MAIN
# ============================================================

def main():
    now = datetime.now(WIB)
    print(f"\n{'='*60}")
    print(f"[{now.strftime('%d %b %Y %H:%M')} WIB] IDR Watch Bot")
    print(f"{'='*60}\n")

    posted_data = load_posted()
    posted_urls = set(posted_data["posted"])
    articles = fetch_articles()
    print(f"📰 Found {len(articles)} articles\n")

    count = 0
    for idx, article in enumerate(articles, 1):
        if count >= MAX_ARTICLES_PER_RUN:
            break

        url = article["url"]
        title = article["title"]
        keyword = article["keyword"]

        print(f"[{idx}] {title[:60]}...")

        if url in posted_urls:
            print(f"    ⊘ Already posted\n")
            continue

        if is_blacklisted(title):
            print(f"    ⊘ Blacklisted\n")
            continue

        if not is_relevant(title, article["summary"]):
            print(f"    ⊘ Not relevant\n")
            continue

        narasi = generate_narasi(title, article["summary"])
        if narasi is None:
            if ON_API_FAIL == "skip":
                print(f"    ⊘ Gemini API failed\n")
                continue

        msg = f"🏦 <b>{title}</b>\n\n{narasi}{FOOTER}"

        image_buf = generate_card_image(title, keyword)
        send_telegram(msg, image_buf)
        print(f"    ✓ Posted!\n")

        posted_urls.add(url)
        posted_data["posted"] = list(posted_urls)
        count += 1

        if count < MAX_ARTICLES_PER_RUN:
            time.sleep(2)

    save_posted(posted_data)
    print(f"{'='*60}")
    print(f"✓ Selesai. {count} artikel dipost.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
