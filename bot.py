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
import feedparser
import requests
from datetime import datetime
import pytz
from google import genai
from urllib.parse import urljoin

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

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
def get_thumbnail(url):
    try:
        # Follow redirect ke URL artikel asli
        resp = requests.get(url, timeout=5, headers={
            "User-Agent": "Mozilla/5.0"
        }, allow_redirects=True)
        
        final_url = resp.url  # URL setelah redirect
        print(f"Final URL: {final_url}")
        
        # Kalau masih di domain google, skip
        if "google.com" in final_url:
            return None
            
        # Cari og:image
        match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', resp.text)
        if not match:
            match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', resp.text)
        if match:
            img_url = match.group(1)
            # Kalau URL relatif, jadiin absolut
            if img_url.startswith("/"):
                from urllib.parse import urlparse
                base = f"{urlparse(final_url).scheme}://{urlparse(final_url).netloc}"
                img_url = base + img_url
            return img_url
    except Exception as e:
        print(f"Thumbnail error: {e}")
    return None

def fetch_articles():
    articles = []
    seen_titles = set()

    for keyword in KEYWORDS:
        url = (
            f"https://news.google.com/rss/search"
            f"?q={requests.utils.quote(keyword)}+when:1d"
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


def send_telegram(msg, image_url=None):
    if image_url:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        payload = {
            "chat_id": TELEGRAM_CHANNEL_ID,
            "photo": image_url,
            "caption": msg,
            "parse_mode": "HTML"
        }
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHANNEL_ID,
            "text": msg,
            "parse_mode": "HTML"
        }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 429:
            retry_after = resp.json().get("parameters", {}).get("retry_after", 10)
            print(f"Telegram rate limit, tunggu {retry_after}s...")
            time.sleep(retry_after)
            requests.post(url, json=payload, timeout=10)
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
        image_url = get_thumbnail(url)
        send_telegram(msg, image_url)
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
