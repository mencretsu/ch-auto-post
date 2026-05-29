# ============================================================
# KONFIGURASI
# ============================================================

# Keyword berita yang dipantau (Google News RSS)
KEYWORDS = [
    "rupiah", "ekonomi Indonesia", "BI rate", "inflasi Indonesia", "kripto","bitcoin","saham","dolar",
    "IHSG", "Bank Indonesia", "Fed rate", "China economy"
]

# Keyword yang diskip (berita ga relevan)
BLACKLIST = [
    "pilkada", "gosip", "artis", "sinetron", "resep", "olahraga"
]

# Maksimum berita diproses per run
MAX_ARTICLES_PER_RUN = 5

# Maksimum URL disimpan di posted.json (auto-trim)
MAX_STORED_URLS = 100

# Panjang narasi (jumlah kalimat)
NARASI_KALIMAT = 3

# Footer tiap post
FOOTER = "\n\n— IDR Watch 🇮🇩"

# Behavior kalau Gemini API gagal: "skip" atau "retry"
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
import google.generativeai as genai

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

POSTED_FILE = "posted.json"
WIB = pytz.timezone("Asia/Jakarta")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")


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
    # Buang suffix sumber berita kayak "- Kompas.com", "| Detik" dll
    title = re.sub(r'\s[-|]\s.*$', '', title).strip()
    return title


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
            if feed.bozo:  # feedparser flag kalau parsing gagal
                print(f"Warning: gagal parse feed untuk '{keyword}'")
                continue
            for entry in feed.entries[:3]:
                title = clean_title(entry.title)
                # Skip duplikat judul mirip
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
        response = model.generate_content(prompt)
        return response.text.strip().upper().startswith("YA")
    except:
        return False

if not is_relevant(title, article["summary"]):
    print(f"Skip (tidak relevan): {title}")
    continue
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
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini error: {e}")
        return None


def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHANNEL_ID,
            "text": msg,
            "parse_mode": "HTML"
        }, timeout=10)
        if resp.status_code == 429:
            # Rate limit — tunggu sesuai retry_after dari Telegram
            retry_after = resp.json().get("parameters", {}).get("retry_after", 10)
            print(f"Telegram rate limit, tunggu {retry_after}s...")
            time.sleep(retry_after)
            # Kirim ulang sekali
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

        narasi = generate_narasi(title, article["summary"])

        if narasi is None:
            if ON_API_FAIL == "skip":
                continue

        msg = f"📰 <b>{title}</b>\n\n{narasi}{FOOTER}"
        send_telegram(msg)
        print(f"Posted: {title}")

        posted_urls.add(url)
        posted_data["posted"] = list(posted_urls)
        count += 1

        # Jeda antar post biar ga spam ke Telegram
        if count < MAX_ARTICLES_PER_RUN:
            time.sleep(2)

    save_posted(posted_data)
    print(f"Selesai. {count} artikel dipost.")


if __name__ == "__main__":
    main()
