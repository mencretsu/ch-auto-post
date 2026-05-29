# ============================================================
# IDR WATCH BOT - NO WATERMARK VERSION
# ============================================================
import os
import json
import time
import re
import io
import feedparser
import requests
from datetime import datetime
from urllib.parse import urlparse, unquote
import pytz
from PIL import Image, ImageDraw, ImageFont
from google import genai

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
# GEMINI
# ============================================================
_gemini_key_index = 0

def get_gemini_client():
    global _gemini_key_index
    key = GEMINI_KEYS[_gemini_key_index % len(GEMINI_KEYS)]
    return genai.Client(api_key=key)

def gemini(prompt):
    global _gemini_key_index
    for i in range(len(GEMINI_KEYS)):
        try:
            client = get_gemini_client()
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt
            )
            return response.text.strip()
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                print(f"Key {_gemini_key_index + 1} kena limit...")
                _gemini_key_index += 1
                continue
            raise e
    return None

# ============================================================
# IMAGE VALIDATION
# ============================================================
def is_valid_image_url(image_url):
    if not image_url or not image_url.startswith('http'):
        return False

    blocked_domains = [
        'google', 'gstatic', 'ggpht', 'doubleclick',
        'googleusercontent', 'facebook.com', 'instagram.com'
    ]

    blocked_paths = [
        'logo', 'icon', 'favicon', 'avatar', 'ads', '1x1',
        'banner', 'promo', 'iklan', 'sponsor', 'jmd',
        'placeholder', 'default', 'noimage', 'no-image',
        'blank', 'dummy', 'sample'
    ]

    url_lower = image_url.lower()

    for domain in blocked_domains:
        if domain in url_lower:
            return False

    for path in blocked_paths:
        if path in url_lower:
            return False

    # Kompas internal assets
    if 'kompascom' in url_lower and '/kompascom/' in url_lower:
        return False

    return True

def is_valid_image_content(image_bytes):
    """Validasi dimensi gambar — reject banner/tracking pixel/icon"""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        ratio = w / h

        if w < 400 or h < 200:
            print(f"    ✗ Image too small: {w}x{h}")
            return False

        # Banner biasanya ratio > 4:1
        if ratio > 4.0 or ratio < 0.3:
            print(f"    ✗ Suspicious aspect ratio: {ratio:.1f}")
            return False

        return True
    except:
        return False

# ============================================================
# IMAGE FETCHING
# ============================================================
def download_and_resize(image_url):
    """Download, validasi, resize ke 1200x630 — tanpa watermark"""
    try:
        resp = requests.get(image_url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()

        if not is_valid_image_content(resp.content):
            return None

        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        img = img.resize((1200, 630), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        buf.seek(0)
        print(f"✓ Image resized to 1200x630")
        return buf

    except Exception as e:
        print(f"✗ Download/resize error: {e}")
        return None

def scrape_local_news_image(keyword):
    """Scrape og:image dari berita lokal Indonesia"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        # Detik
        search_url = f"https://www.detik.com/search/searchall?query={requests.utils.quote(keyword)}"
        resp = requests.get(search_url, headers=headers, timeout=10)
        resp.raise_for_status()

        match = re.search(
            r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
            resp.text
        )
        if match:
            img_url = match.group(1)
            if is_valid_image_url(img_url):
                print(f"✓ Scraped from Detik: {img_url[:60]}...")
                return img_url

        # Kompas fallback
        search_url = f"https://search.kompas.com/search?q={requests.utils.quote(keyword)}&sort=latest"
        resp = requests.get(search_url, headers=headers, timeout=10)
        resp.raise_for_status()

        match = re.search(
            r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
            resp.text
        )
        if match:
            img_url = match.group(1)
            if is_valid_image_url(img_url):
                print(f"✓ Scraped from Kompas: {img_url[:60]}...")
                return img_url

        return None

    except Exception as e:
        print(f"⚠ Scrape local news error: {e}")
        return None

def create_stylish_placeholder(title, keyword=""):
    """Gradient placeholder — tanpa watermark"""
    try:
        emoji_map = {
            "rupiah": "💱", "USD": "$", "BI": "🏛️", "inflasi": "📈",
            "IHSG": "📊", "Fed": "🇺🇸", "bitcoin": "₿", "emas": "🏆",
            "minyak": "🛢️", "nikel": "⚙️", "ekonomi": "💼", "rate": "📉"
        }

        emoji = "📰"
        for key, emj in emoji_map.items():
            if key.lower() in title.lower() or key.lower() in keyword.lower():
                emoji = emj
                break

        img = Image.new("RGB", (1200, 630))
        pixels = img.load()

        title_lower = title.lower()
        if any(w in title_lower for w in ["naik", "positif", "menguat", "surplus"]):
            color_start = (76, 175, 80)
            color_end = (27, 94, 32)
        elif any(w in title_lower for w in ["turun", "negatif", "melemah", "defisit"]):
            color_start = (244, 67, 54)
            color_end = (183, 28, 28)
        else:
            color_start = (33, 150, 243)
            color_end = (13, 71, 161)

        for y in range(630):
            ratio = y / 630
            r = int(color_start[0] * (1 - ratio) + color_end[0] * ratio)
            g = int(color_start[1] * (1 - ratio) + color_end[1] * ratio)
            b = int(color_start[2] * (1 - ratio) + color_end[2] * ratio)
            for x in range(1200):
                pixels[x, y] = (r, g, b)

        draw = ImageDraw.Draw(img)

        try:
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 56)
            emoji_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 120)
        except:
            title_font = ImageFont.load_default()
            emoji_font = ImageFont.load_default()

        # Emoji
        emoji_bbox = draw.textbbox((0, 0), emoji, font=emoji_font)
        emoji_w = emoji_bbox[2] - emoji_bbox[0]
        emoji_x = (1200 - emoji_w) // 2
        draw.text((emoji_x, 100), emoji, font=emoji_font, fill=(255, 255, 255))

        # Wrap title
        words = title.split()
        lines = []
        current_line = []

        for word in words:
            current_line.append(word)
            test_line = " ".join(current_line)
            bbox = draw.textbbox((0, 0), test_line, font=title_font)
            if bbox[2] - bbox[0] > 1100:
                lines.append(" ".join(current_line[:-1]))
                current_line = [word]

        if current_line:
            lines.append(" ".join(current_line))

        lines = lines[:3]

        y_offset = 280
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=title_font)
            text_w = bbox[2] - bbox[0]
            x = (1200 - text_w) // 2
            draw.text((x, y_offset), line, font=title_font, fill=(255, 255, 255))
            y_offset += 80

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        buf.seek(0)
        print(f"✓ Stylish placeholder created ({emoji})")
        return buf

    except Exception as e:
        print(f"✗ Placeholder creation error: {e}")
        return None

def fetch_article_image_smart(article_url, title, keyword):
    """
    Fallback strategy:
    1. Extract og:image dari artikel
    2. Scrape dari berita lokal Indonesia
    3. Generate gradient placeholder
    """
    print(f"    📸 Fetching image:")

    # Strategy 1: og:image dari artikel
    print(f"       → Trying article extraction...")
    try:
        resp = requests.get(article_url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()

        match = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', resp.text)
        if match:
            img_url = match.group(1)
            if is_valid_image_url(img_url):
                image_buf = download_and_resize(img_url)
                if image_buf:
                    return image_buf
    except:
        pass

    # Strategy 2: Scrape berita lokal
    print(f"       → Trying local Indonesia news scrape...")
    scraped_image = scrape_local_news_image(keyword)
    if scraped_image:
        image_buf = download_and_resize(scraped_image)
        if image_buf:
            return image_buf

    # Strategy 3: Gradient placeholder
    print(f"       → Creating stylish placeholder...")
    return create_stylish_placeholder(title, keyword)

# ============================================================
# HELPERS
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
    title = re.sub(r'\s[-|]\s.*$', '', title).strip()
    return title

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

        image_buf = fetch_article_image_smart(url, title, keyword)
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
