# ============================================================
# KONFIGURASI
# ============================================================

KEYWORDS = [
    "rupiah", "ekonomi Indonesia", "BI rate", "inflasi Indonesia", "kripto", "bitcoin", "saham", "dolar",
    "IHSG", "Bank Indonesia", "Fed rate", "China economy"
]

BLACKLIST = [
    "pilkada", "gosip", "artis", "sinetron", "resep", "olahraga"
]

MAX_ARTICLES_PER_RUN = 1
MAX_STORED_URLS = 100
FOOTER = "\n\n— IDR Watch 🇮🇩"
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
from urllib.parse import urlparse
import pytz
from PIL import Image, ImageDraw, ImageFont
from google import genai

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
PEXELS_API_KEY = os.environ["PEXELS_API_KEY"]

POSTED_FILE = "posted.json"
WIB = pytz.timezone("Asia/Jakarta")

client = genai.Client(api_key=GEMINI_API_KEY)


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
                articles.append({
                    "title": title,
                    "url": entry.link,
                    "summary": re.sub(r'<[^>]+>', '', getattr(entry, "summary", "")),
                })
        except Exception as e:
            print(f"Error fetch '{keyword}': {e}")
            continue

    return articles


def is_blacklisted(title):
    return any(bl.lower() in title.lower() for bl in BLACKLIST)


def gemini(prompt):
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return response.text.strip()


def is_relevant(title, summary):
    prompt = f"""
Kamu kurator channel ekonomi Indonesia. Tentukan apakah berita ini cukup penting dan relevan untuk dipost ke channel.

Judul: {title}
Ringkasan: {summary}

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
    except Exception as e:
        print(f"Gemini error: {e}")
        return None


def extract_keyword(title):
    stopwords = {"di", "ke", "dari", "yang", "dan", "atau", "dengan", "untuk",
                 "ini", "itu", "pada", "adalah", "akan", "jika", "karena",
                 "per", "jadi", "bisa", "ada", "tidak", "lebih", "sudah"}
    words = [w for w in title.lower().split() if w not in stopwords]
    return " ".join(words[:3])


def search_image(keyword):
    try:
        url = "https://api.pexels.com/v1/search"
        resp = requests.get(url, params={
            "query": keyword,
            "per_page": 1,
            "orientation": "landscape"
        }, headers={
            "Authorization": PEXELS_API_KEY
        }, timeout=10)
        data = resp.json()
        if data.get("photos"):
            return data["photos"][0]["src"]["large"]
    except Exception as e:
        print(f"Pexels error: {e}")
    return None


def add_watermark(image_url, watermark_text="IDR Watch 🇮🇩"):
    try:
        resp = requests.get(image_url, timeout=10)
        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        img = img.resize((1200, 630), Image.LANCZOS)

        # Overlay gelap di bawah
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
        return buf

    except Exception as e:
        print(f"Watermark error: {e}")
        return None


def send_telegram(msg, image_buf=None):
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
                print(f"Telegram rate limit, tunggu {retry_after}s...")
                time.sleep(retry_after)
                image_buf.seek(0)
                requests.post(url, data={
                    "chat_id": TELEGRAM_CHANNEL_ID,
                    "caption": msg,
                    "parse_mode": "HTML"
                }, files={
                    "photo": ("image.jpg", image_buf, "image/jpeg")
                }, timeout=15)
        except Exception as e:
            print(f"Telegram error: {e}")
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            requests.post(url, json={
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": msg,
                "parse_mode": "HTML"
            }, timeout=10)
        except Exception as e:
            print(f"Telegram error: {e}")


def main():
    now = datetime.now(WIB)
    print(f"[{now.strftime('%d %b %Y %H:%M')} WIB] Bot jalan...")

    posted_data = load_posted()
    posted_urls = set(posted_data["posted"])

    articles = fetch_articles()
    count = 0

    for article in articles:
        if count >= MAX_ARTICLES_PER_RUN:
            break

        url = article["url"]
        title = article["title"]

        if url in posted_urls:
            continue

        if is_blacklisted(title):
            continue

        if not is_relevant(title, article["summary"]):
            print(f"Skip (tidak relevan): {title}")
            continue

        narasi = generate_narasi(title, article["summary"])

        if narasi is None:
            if ON_API_FAIL == "skip":
                continue

        msg = f"📰 <b>{title}</b>\n\n{narasi}{FOOTER}"

        keyword = extract_keyword(title)
        image_url = search_image(keyword)
        image_buf = add_watermark(image_url) if image_url else None

        send_telegram(msg, image_buf)
        print(f"Posted: {title}")

        posted_urls.add(url)
        posted_data["posted"] = list(posted_urls)
        count += 1

        if count < MAX_ARTICLES_PER_RUN:
            time.sleep(2)

    save_posted(posted_data)
    print(f"Selesai. {count} artikel dipost.")


if __name__ == "__main__":
    main()
