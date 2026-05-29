# ============================================================
# IDR WATCH - ALTERNATIF IMAGE SOLUTIONS (FIXED)
# ============================================================
# User cape dengan API eksternal, pakai solusi lokal & scraping
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
import random

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
# OPTION 1: SCRAPE DARI BERITA LOKAL INDONESIA
# ============================================================
INDONESIAN_NEWS_SOURCES = {
    "detik": {
        "url": "https://www.detik.com/berita",
        "og_image": True,
        "priority": 1
    },
    "kompas": {
        "url": "https://www.kompas.com/",
        "og_image": True,
        "priority": 2
    },
    "cnnid": {
        "url": "https://www.cnnindonesia.com/",
        "og_image": True,
        "priority": 1
    },
    "bisnis": {
        "url": "https://bisnis.com.au/",
        "og_image": True,
        "priority": 2
    }
}

# ============================================================
# IMPROVED IMAGE VALIDATION (FILTER KETAT)
# ============================================================

def is_valid_image_url(image_url):
    """
    Validate image URL dengan filter KETAT
    Prevent banner, asset, logo, dll yang bukan artikel image
    """
    if not image_url or not image_url.startswith('http'):
        return False
    
    url_lower = image_url.lower()
    
    # ❌ BLACKLIST DOMAIN
    blocked_domains = [
        'google', 'gstatic', 'ggpht', 'doubleclick',
        'googleusercontent', 'facebook.com', 'instagram.com'
    ]
    
    # ❌ BLACKLIST PATH PATTERNS (banner, asset, dll)
    blocked_paths = [
        'logo',              # Logo
        'icon',              # Icon
        'favicon',           # Browser icon
        'avatar',            # User avatar
        'ads',               # Ads
        '1x1',               # Tracking pixel
        'banner',            # ← Banner ads/promotional
        '/data/',            # ← Data folder (biasanya asset like Kompas)
        'asset',             # ← Asset folder
        'static/',           # ← Static folder
        'jmd',               # ← Kompas JMD template  
        'template',          # ← Template images
        'placeholder',       # ← Placeholder
        'stock/',            # ← Stock images generic
        'default',           # ← Default images
        'fallback',          # ← Fallback
        'social-share',      # ← Social share buttons
        'btn-',              # ← Button images
        'button-',
        'bg-',               # ← Background pattern
        'x128', 'x64', 'x32', # ← Small dimensions
        '-thumb',            # ← Thumbnail
        'thumbnail',
        '_sm', '_small',     # ← Small size variant
        'generic',
        'blank',
        'noimage',
        'no-image',
        'spacer',
        'pixel',
        'tracker',
        'v2/image',          # ← Generic image API
        'images/generic',
    ]
    
    # Check domains
    for domain in blocked_domains:
        if domain in url_lower:
            return False
    
    # Check paths
    for path in blocked_paths:
        if path in url_lower:
            print(f"      ⊘ Blocked path: {path}")
            return False
    
    # ✓ Minimal filename length check
    parsed = urlparse(image_url)
    filename = parsed.path.split('/')[-1]
    
    if len(filename) < 8:
        print(f"      ⊘ Filename terlalu pendek: {filename}")
        return False
    
    # ✓ Heuristic: Skip suspicious pattern
    suspicious_patterns = [
        'widget',
        'component',
        'module',
        'helper',
    ]
    
    for pattern in suspicious_patterns:
        if pattern in url_lower:
            return False
    
    return True


def validate_image_before_use(image_buf):
    """
    Validate actual image file sebelum digunakan
    Untuk extra safety: check dimensi, aspect ratio
    """
    try:
        image_buf.seek(0)
        img = Image.open(image_buf)
        
        width, height = img.size
        
        # Check ukuran minimal
        if width < 400 or height < 300:
            print(f"      ⊘ Image terlalu kecil: {width}x{height}")
            return False
        
        # Aspect ratio check (banner biasanya extreme)
        ratio = width / height
        
        if ratio > 4 or ratio < 0.25:
            print(f"      ⊘ Aspect ratio suspicious: {ratio:.2f}")
            return False
        
        print(f"      ✓ Image valid: {width}x{height}")
        return True
        
    except Exception as e:
        print(f"      ⚠ Image validation error: {e}")
        return False

# ============================================================
# SCRAPE DARI BERITA LOKAL INDONESIA
# ============================================================

def scrape_local_news_image(keyword):
    """
    Scrape image dari berita lokal Indonesia
    Lebih reliable daripada API eksternal karena:
    - Struktur HTML konsisten
    - og:image selalu ada
    - Tidak perlu API key
    """
    try:
        # Search di Detik (paling reliable)
        search_url = f"https://www.detik.com/search/searchall?query={requests.utils.quote(keyword)}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        resp = requests.get(search_url, headers=headers, timeout=10)
        resp.raise_for_status()
        
        # Cari og:image dari first search result
        match = re.search(
            r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
            resp.text
        )
        
        if match:
            img_url = match.group(1)
            if is_valid_image_url(img_url):
                try:
                    img_resp = requests.get(img_url, timeout=10, headers=headers)
                    if validate_image_before_use(io.BytesIO(img_resp.content)):
                        print(f"      ✓ Scraped from Detik: {img_url[:60]}...")
                        return img_url
                except:
                    pass
        
        # Fallback ke Kompas
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
                try:
                    img_resp = requests.get(img_url, timeout=10, headers=headers)
                    if validate_image_before_use(io.BytesIO(img_resp.content)):
                        print(f"      ✓ Scraped from Kompas: {img_url[:60]}...")
                        return img_url
                except:
                    pass
        
        return None
        
    except Exception as e:
        print(f"      ⚠ Scrape local news error: {e}")
        return None

# ============================================================
# OPTION 2: GENERATE IMAGE PAKAI GEMINI (SIMPLE)
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
                print(f"      Key {_gemini_key_index + 1} kena limit...")
                _gemini_key_index += 1
                continue
            raise e
    return None

def generate_image_with_gemini(title, summary):
    """
    Generate image description, nanti bisa pakai untuk:
    1. Request ke Gemini ImageGen (kalau available)
    2. Atau maintain detailed fallback image dengan info ini
    
    For now: return description untuk fallback image
    """
    try:
        prompt = f"""
Berdasarkan berita ini, jelaskan visual yang cocok untuk dipajang:
Judul: {title}
Summary: {summary}
Format: HANYA DESKRIPSI VISUAL (misal: "grafik kenaikan rupiah dengan warna merah")
Jawab SINGKAT hanya 1 kalimat visual yang bisa di-represent dengan warna/bentuk/emoji
"""
        result = gemini(prompt)
        return result
    except:
        return None

# ============================================================
# OPTION 3: CREATE STYLISH PLACEHOLDER (NO EXTERNAL API)
# ============================================================

def create_stylish_placeholder(title, keyword="", watermark_text="@idrwatch"):
    """
    Placeholder yang lebih menarik:
    - Gradient background
    - Emoji related to keyword
    - Better typography
    - Ala Watcher.Guru style
    """
    try:
        # Emoji mapping untuk keywords
        emoji_map = {
            "rupiah": "💱", "USD": "$", "BI": "🏛️", "inflasi": "📈",
            "IHSG": "📊", "Fed": "🇺🇸", "bitcoin": "₿", "emas": "🏆",
            "minyak": "🛢️", "nikel": "⚙️", "ekonomi": "💼", "rate": "📉"
        }
        
        # Find matching emoji
        emoji = "📰"
        for key, emj in emoji_map.items():
            if key.lower() in title.lower() or key.lower() in keyword.lower():
                emoji = emj
                break
        
        # Create image 1200x630
        img = Image.new("RGB", (1200, 630))
        
        # Gradient background (dynamic berdasarkan keyword)
        pixels = img.load()
        if "naik" in title.lower() or "positif" in title.lower():
            # Green gradient (bullish)
            color_start = (76, 175, 80)
            color_end = (27, 94, 32)
        elif "turun" in title.lower() or "negatif" in title.lower():
            # Red gradient (bearish)
            color_start = (244, 67, 54)
            color_end = (183, 28, 28)
        else:
            # Blue gradient (neutral)
            color_start = (33, 150, 243)
            color_end = (13, 71, 161)
        
        # Apply gradient
        for y in range(630):
            ratio = y / 630
            r = int(color_start[0] * (1 - ratio) + color_end[0] * ratio)
            g = int(color_start[1] * (1 - ratio) + color_end[1] * ratio)
            b = int(color_start[2] * (1 - ratio) + color_end[2] * ratio)
            for x in range(1200):
                pixels[x, y] = (r, g, b)
        
        draw = ImageDraw.Draw(img)
        
        # Load fonts
        try:
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 56)
            emoji_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 120)
            watermark_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
        except:
            title_font = ImageFont.load_default()
            emoji_font = ImageFont.load_default()
            watermark_font = ImageFont.load_default()
        
        # Draw emoji di tengah atas
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
        
        lines = lines[:3]  # Max 3 lines
        
        # Draw title
        y_offset = 280
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=title_font)
            text_w = bbox[2] - bbox[0]
            x = (1200 - text_w) // 2
            draw.text((x, y_offset), line, font=title_font, fill=(255, 255, 255))
            y_offset += 80
        
        # Draw watermark
        bbox = draw.textbbox((0, 0), watermark_text, font=watermark_font)
        text_w = bbox[2] - bbox[0]
        x = 1200 - text_w - 20
        y = 630 - 45
        draw.text((x, y), watermark_text, font=watermark_font, fill=(255, 255, 255))
        
        # Save to buffer
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        buf.seek(0)
        print(f"      ✓ Stylish placeholder created ({emoji})")
        return buf
        
    except Exception as e:
        print(f"      ✗ Placeholder creation error: {e}")
        return None

# ============================================================
# ADD WATERMARK TO IMAGE
# ============================================================

def add_watermark(image_url, watermark_text="@idrwatch"):
    """Resize dan add watermark ke image"""
    try:
        resp = requests.get(image_url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        
        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        img = img.resize((1200, 630), Image.LANCZOS)
        
        # Overlay gelap
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rectangle(
            [(0, img.height - 80), (img.width, img.height)],
            fill=(0, 0, 0, 160)
        )
        img = Image.alpha_composite(img, overlay)
        
        # Watermark
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        except:
            font = ImageFont.load_default()
        
        bbox = draw.textbbox((0, 0), watermark_text, font=font)
        text_w = bbox[2] - bbox[0]
        x = img.width - text_w - 20
        y = img.height - 55
        draw.text((x, y), watermark_text, font=font, fill=(255, 255, 255, 255))
        
        img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        buf.seek(0)
        print(f"      ✓ Watermark added")
        return buf
    except Exception as e:
        print(f"      ✗ Watermark error: {e}")
        return None

# ============================================================
# SMART IMAGE FETCHING WITH FALLBACK
# ============================================================

def fetch_article_image_smart(article_url, title, keyword):
    """
    Smart image fetching dengan fallback strategy:
    1. Coba extract dari artikel (dengan validasi ketat)
    2. Kalau gagal, scrape dari berita lokal Indonesia
    3. Kalau masih gagal, create stylish placeholder
    """
    
    print(f"    📸 Fetching image strategy:")
    
    # Strategy 1: Extract dari artikel
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
                try:
                    img_resp = requests.get(img_url, timeout=10)
                    if validate_image_before_use(io.BytesIO(img_resp.content)):
                        image_buf = add_watermark(img_url)
                        if image_buf:
                            return image_buf
                except:
                    pass
    except:
        pass
    
    # Strategy 2: Scrape dari berita lokal Indonesia
    print(f"       → Trying local Indonesia news scrape...")
    scraped_image = scrape_local_news_image(keyword)
    if scraped_image:
        try:
            image_buf = add_watermark(scraped_image)
            if image_buf:
                return image_buf
        except:
            pass
    
    # Strategy 3: Create stylish placeholder (paling reliable!)
    print(f"       → Creating stylish placeholder...")
    return create_stylish_placeholder(title, keyword)

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
    print(f"[{now.strftime('%d %b %Y %H:%M')} WIB] IDR Watch Bot (Smart Images)")
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
        
        # SMART IMAGE FETCHING
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
