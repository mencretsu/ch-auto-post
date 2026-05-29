# ============================================================
# IDR WATCH - TELEGRAM ECONOMICS BOT (FIXED)
# ============================================================

KEYWORDS = [
    # Indonesia
    "rupiah",
    "USD/IDR",
    "BI rate",
    "Bank Indonesia",
    "inflasi Indonesia",
    "IHSG",

    # Global macro
    "Fed rate",
    "FOMC",
    "dolar AS",
    "yield Treasury",

    # Crypto
    "bitcoin",
    "ethereum",
    "kripto",

    # China
    "China economy",
    "ekonomi China",
    "yuan",

    # Commodities
    "emas",
    "minyak",
    "nikel"
]

BLACKLIST = [
    "pilkada", "gosip", "artis", "sinetron", "resep", "olahraga"
]

MAX_ARTICLES_PER_RUN = 1
MAX_STORED_URLS = 100
FOOTER = '\n\n— <a href="https://t.me/idrwatch">IDR Watch 🇮🇩</a>'
ON_API_FAIL = "skip"

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
import random

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]

POSTED_FILE = "posted.json"
WIB = pytz.timezone("Asia/Jakarta")
GEMINI_KEYS = [
    os.environ["GEMINI_API_KEY_1"],
    os.environ["GEMINI_API_KEY_2"],
    os.environ["GEMINI_API_KEY_3"],
]


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
    """
    Extract real article URL from Google News redirect
    Google News format: https://news.google.com/url?q=REAL_URL_ENCODED
    """
    try:
        # Method 1: Parse query parameter q=
        match = re.search(r'[?&]q=([^&]+)', google_news_url)
        if match:
            real_url = unquote(match.group(1))
            if real_url.startswith('http'):
                print(f"✓ Real URL extracted: {real_url[:70]}...")
                return real_url
    except Exception as e:
        print(f"⚠ URL extraction error: {e}")
    
    # Fallback
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
                print(f"Warning: gagal parse feed untuk '{keyword}'")
                continue
            for entry in feed.entries[:3]:
                title = clean_title(entry.title)
                if title.lower() in seen_titles:
                    continue
                seen_titles.add(title.lower())
                
                # EXTRACT REAL ARTICLE URL (bukan Google redirect)
                real_url = extract_real_url_from_google_news(entry.link)
                
                articles.append({
                    "title": title,
                    "url": real_url,
                    "summary": re.sub(r'<[^>]+>', '', getattr(entry, "summary", "")),
                })
        except Exception as e:
            print(f"Error fetch '{keyword}': {e}")
            continue

    return articles


def is_blacklisted(title):
    return any(bl.lower() in title.lower() for bl in BLACKLIST)


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
                print(f"Key {_gemini_key_index + 1} kena limit, rotate ke key berikutnya...")
                _gemini_key_index += 1
                continue
            raise e
    print("Semua Gemini key kena limit!")
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
- Jangan ulangi atau parafrase judul di kalimat pertama, langsung ke konteks atau dampaknya
"""
    try:
        return gemini(prompt)
    except Exception as e:
        print(f"Gemini error: {e}")
        return None


def is_valid_image_url(image_url):
    """Validate image URL - exclude Google/logo/favicon images"""
    if not image_url or not image_url.startswith('http'):
        return False
    
    # Block suspicious domains
    blocked_domains = [
        'google', 'gstatic', 'ggpht', 'doubleclick', 'google.com',
        'googleusercontent', 'facebook.com', 'instagram.com',
        'twitter.com', 'telegram', 'gravatar'
    ]
    
    # Block suspicious paths
    blocked_paths = [
        'logo', 'icon', 'favicon', 'avatar', 'profile', 'user', 
        'ads', 'ad.', '.gif', '1x1', 'placeholder', 'blank'
    ]
    
    url_lower = image_url.lower()
    
    # Check domains
    for domain in blocked_domains:
        if domain in url_lower:
            return False
    
    # Check paths
    for path in blocked_paths:
        if path in url_lower:
            return False
    
    # Check image dimensions (1x1 atau terlalu kecil = logo)
    if '1x1' in url_lower or 'width=1' in url_lower or 'height=1' in url_lower:
        return False
    
    return True


def fetch_article_image(article_url):
    """Extract gambar dari artikel link"""
    try:
        resp = requests.get(article_url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        resp.raise_for_status()
        
        # Priority 1: og:image (paling reliable untuk news articles)
        match = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', resp.text)
        if match:
            img_url = match.group(1)
            if is_valid_image_url(img_url):
                print(f"✓ Found og:image: {img_url[:60]}...")
                return img_url
            else:
                print(f"⊘ og:image blocked (suspicious): {img_url[:60]}...")
        
        # Priority 2: twitter:image
        match = re.search(r'<meta\s+name=["\']twitter:image["\']\s+content=["\']([^"\']+)["\']', resp.text)
        if match:
            img_url = match.group(1)
            if is_valid_image_url(img_url):
                print(f"✓ Found twitter:image: {img_url[:60]}...")
                return img_url
            else:
                print(f"⊘ twitter:image blocked (suspicious): {img_url[:60]}...")
        
        # Priority 3: article:image (untuk news articles)
        match = re.search(r'<meta\s+property=["\']article:image["\']\s+content=["\']([^"\']+)["\']', resp.text)
        if match:
            img_url = match.group(1)
            if is_valid_image_url(img_url):
                print(f"✓ Found article:image: {img_url[:60]}...")
                return img_url
            else:
                print(f"⊘ article:image blocked (suspicious): {img_url[:60]}...")
        
        # Priority 4: img tag dengan alt text yang masuk akal
        img_matches = re.findall(r'<img[^>]+src=["\']([^"\']+)["\'][^>]*alt=["\']([^"\']+)["\']', resp.text)
        for url, alt_text in img_matches:
            if url.startswith('http') and len(alt_text) > 5 and is_valid_image_url(url):
                print(f"✓ Found img tag: {url[:60]}...")
                return url
        
        # Priority 5: img tag tanpa syarat tapi validate
        img_matches = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', resp.text)
        for url in img_matches:
            if is_valid_image_url(url):
                print(f"✓ Found valid img tag: {url[:60]}...")
                return url
        
        print(f"✗ No valid image found in article")
        return None
        
    except Exception as e:
        print(f"✗ Article image extraction error: {e}")
        return None


def create_placeholder_image(title, watermark_text="@idrwatch"):
    """Create placeholder image kalau artikel ga punya gambar"""
    try:
        # Buat image 1200x630 dengan gradient background
        img = Image.new("RGB", (1200, 630), color=(33, 150, 243))
        draw = ImageDraw.Draw(img)
        
        # Overlay warna
        overlay = Image.new("RGBA", img.size, (0, 100, 200, 80))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        
        # Font
        try:
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
            watermark_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
        except:
            title_font = ImageFont.load_default()
            watermark_font = ImageFont.load_default()
        
        # Wrap text title
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
        
        # Draw title (max 3 lines)
        lines = lines[:3]
        total_height = len(lines) * 60
        y_offset = (630 - total_height) // 2
        
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=title_font)
            text_w = bbox[2] - bbox[0]
            x = (1200 - text_w) // 2
            draw.text((x, y_offset), line, font=title_font, fill=(255, 255, 255))
            y_offset += 70
        
        # Draw watermark at bottom
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), watermark_text, font=watermark_font)
        text_w = bbox[2] - bbox[0]
        x = 1200 - text_w - 20
        y = 630 - 45
        draw.text((x, y), watermark_text, font=watermark_font, fill=(255, 255, 255))
        
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        buf.seek(0)
        print(f"✓ Placeholder image created")
        return buf
        
    except Exception as e:
        print(f"✗ Placeholder creation error: {e}")
        return None


def add_watermark(image_url, watermark_text="@idrwatch"):
    """Resize image dan add watermark"""
    if not image_url:
        return None
    
    try:
        resp = requests.get(image_url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        
        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        img = img.resize((1200, 630), Image.LANCZOS)

        # Overlay gelap di bawah untuk readability
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
        print(f"✓ Watermark added")
        return buf

    except Exception as e:
        print(f"✗ Watermark error: {e}")
        return None


def send_telegram(msg, image_buf=None):
    """Send message to Telegram channel"""
    if image_buf:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        try:
            resp = requests.post(url, data={
                "chat_id": TELEGRAM_CHANNEL_ID,
                "caption": msg,
                "parse_mode": "HTML"
            }, files={
                "photo": ("image.jpg", image_buf, "image/jpeg")
            }, timeout=15)
            
            if resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 10)
                print(f"⚠ Telegram rate limit, tunggu {retry_after}s...")
                time.sleep(retry_after)
                image_buf.seek(0)
                requests.post(url, data={
                    "chat_id": TELEGRAM_CHANNEL_ID,
                    "caption": msg,
                    "parse_mode": "HTML"
                }, files={
                    "photo": ("image.jpg", image_buf, "image/jpeg")
                }, timeout=15)
            
            print(f"✓ Telegram photo sent")
            return True
            
        except Exception as e:
            print(f"✗ Telegram photo error: {e}, fallback to text-only")
            return send_telegram(msg)
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            requests.post(url, json={
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": msg,
                "parse_mode": "HTML"
            }, timeout=10)
            print(f"✓ Telegram text sent")
            return True
        except Exception as e:
            print(f"✗ Telegram error: {e}")
            return False


def main():
    now = datetime.now(WIB)
    print(f"\n{'='*60}")
    print(f"[{now.strftime('%d %b %Y %H:%M')} WIB] Bot jalan...")
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
        
        print(f"[{idx}] Processing: {title[:70]}...")

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

        # EXTRACT GAMBAR DARI ARTIKEL
        print(f"    → Extracting image...")
        image_url = fetch_article_image(url)
        image_buf = None
        
        # Try add watermark if valid image found
        if image_url:
            image_buf = add_watermark(image_url)
        
        # FALLBACK KE PLACEHOLDER KALAU GAMBAR KOSONG ATAU SUSPICIOUS
        if not image_buf:
            print(f"    → Creating placeholder image instead...")
            image_buf = create_placeholder_image(title)

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
