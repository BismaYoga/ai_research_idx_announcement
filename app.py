"""
IDX Keterbukaan Informasi -> Notifikasi Telegram + AI Insight
Mode Lokal: Direct Connection (Tanpa Proxy)
===========================================================================
"""

import json
import re
import sys
import time
import os
import html
import tempfile
import threading
from datetime import datetime
from collections import deque
import gc

import requests
from google import genai
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from contextlib import asynccontextmanager

# Web Dashboard (FastAPI / Uvicorn)
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

# ---------------------------------------------------------------------------
# AUTO LOAD .ENV
# ---------------------------------------------------------------------------

def load_dotenv(filepath: str = ".env"):
    """Membaca file .env dan memasukkannya ke os.environ secara otomatis"""
    if not os.path.exists(filepath):
        return
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("\"'").strip()
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception as e:
        print(f"Notice load .env: {e}")

load_dotenv()

# ---------------------------------------------------------------------------
# KONFIGURASI DARI .ENV
# ---------------------------------------------------------------------------

URL = os.getenv("IDX_URL", "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/").strip()
API_AI = os.getenv("API_AI", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))
PAGES_TO_SCAN = int(os.getenv("PAGES_TO_SCAN", "2"))
SEEN_FILE = os.getenv("SEEN_FILE", "seen_ids.json").strip()
MAX_SEEN = int(os.getenv("MAX_SEEN", "5000"))

PORT = int(os.getenv("PORT", "7860"))
HOST = os.getenv("HOST", "0.0.0.0").strip()
HEADLESS = os.getenv("HEADLESS", "true").strip().lower() in ["true", "1", "yes"]

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID or not API_AI:
    print("\n⚠️  [PERINGATAN] Variabel rahasia belum lengkap di file .env!")
    if not TELEGRAM_TOKEN:
        print("   - TELEGRAM_TOKEN masih kosong")
    if not TELEGRAM_CHAT_ID:
        print("   - TELEGRAM_CHAT_ID masih kosong")
    if not API_AI:
        print("   - API_AI masih kosong")
    print("   Silakan lengkapi file .env Anda.\n")

# Live Logs untuk Web Dashboard
bot_logs = deque(maxlen=100)
bot_status = {
    "state": "Inisialisasi...",
    "last_scan": "-",
    "total_sent": 0,
    "last_items": []
}

def log_event(msg: str):
    stamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        try:
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass
    bot_logs.append(line)

# ---------------------------------------------------------------------------
# FILTER PENGUMUMAN (BLACKLIST)
# ---------------------------------------------------------------------------

BLACKLIST_PREFIXES = [
    "penjelasan atas volatilitas transaksi",
    "penjelasan atas keterlambatan",
    "penyampaian laporan keuangan",
    "penyampaian bukti iklan",
    "penyampaian materi public expose",
    "penjelasan atas permintaan penjelasan bursa",
    "pencatatan saham",
    "pengumuman bursa",
    "penyesuaian structured warrant",
    "penyesuaian jumlah structured warrant",
    "pengumuman kepemilikan saham",
    "pembukaan penghentian sementara perdagangan efek",
    "penghentian sementara perdagangan efek",
    "rencana penyelenggaraan public expose",
    "pemberitahuan hasil rupo",
    "perubahan internal audit",
    "perubahan corporate secretary",
    "perubahan pengurus",
    "perubahan anggota",
    "perubahan komite",
    "laporan harian",
    "laporan pengalihan kembali saham hasil buy back",
    "laporan hasil public expose",
    "laporan kepemilikan",
    "laporan jumlah",
    "laporan penggunaan",
    "ringkasan risalah rapat",
    "jatuh tempo surat berharga negara",
    "saham hilang",
    "laporan bulanan",
    "laporan tahunan",
]

SIARAN_PERS_PREFIXES = [
    "press release",
    "pers release",
    "siaran pers",
]

def normalize(text: str) -> str:
    text = text.replace("\n", " ").replace("\t", " ").replace("\r", " ")
    return re.sub(r"\s+", " ", text).strip().lower()

def is_blacklisted(judul: str) -> bool:
    j = normalize(judul)
    return any(j.startswith(prefix) for prefix in BLACKLIST_PREFIXES)

def is_siaran_pers(judul: str) -> bool:
    j = normalize(judul)
    return any(j.startswith(prefix) for prefix in SIARAN_PERS_PREFIXES)

def extract_emiten(judul_raw: str) -> str:
    m = re.search(r"\[([^\]]*)\]", judul_raw)
    return normalize(m.group(1)).upper() if m else ""

def judul_bersih(judul_raw: str) -> str:
    judul = re.sub(r"\[[^\]]*\]", "", judul_raw).replace("\n", " ")
    return re.sub(r"\s+", " ", judul).strip()

def extract_id(card, link_utama: str) -> str:
    lampiran = card.query_selector_all("ul.list-nostyle li a")
    for a in lampiran:
        href = a.get_attribute("download") or a.get_attribute("href") or ""
        m = re.search(r"_(\d{6,})_", href)
        if m:
            return m.group(1)
    return link_utama or ""

def extract_lampiran(card) -> list:
    lampiran = []
    links = card.query_selector_all("ul.list-nostyle li a")
    for a in links:
        link = a.get_attribute("href") or a.get_attribute("download") or ""
        if link:
            if link.startswith("/"):
                link = f"https://www.idx.co.id{link}"
            if link not in lampiran:
                lampiran.append(link)
    return lampiran

# ---------------------------------------------------------------------------
# PERSISTENSI ID (PENCEGAH DUPLIKASI NOTIFIKASI)
# ---------------------------------------------------------------------------

def load_seen() -> set:
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_seen(seen: set):
    data = list(seen)[-MAX_SEEN:]
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        log_event(f"Gagal simpan seen_ids: {e}")

# ---------------------------------------------------------------------------
# GEMINI AI CLIENT
# ---------------------------------------------------------------------------

ai_client = genai.Client(api_key=API_AI)

AI_PROMPT = """Kamu adalah analis pasar modal Indonesia yang ahli.

Analisis dokumen keterbukaan informasi IDX berikut dan berikan insight singkat.

Format jawaban (WAJIB ikuti, maksimal 3-4 kalimat):

📊 <b>Insight AI:</b>
[Ringkasan inti dokumen — apa yang terjadi]
[Dampak/implikasi bagi investor]
[Sentimen: Positif 🟢 / Netral 🟡 / Negatif 🔴]

Aturan:
- Gunakan bahasa Indonesia yang ringkas dan mudah dipahami
- Fokus pada hal MATERIAL bagi investor
- Jangan ulangi judul dokumen
- JANGAN gunakan markdown (**, ##, dll), gunakan HTML tags (<b>, <i>) saja
- Maksimal 300 karakter"""

def download_pdf(page, url: str) -> bytes:
    if not url:
        return None
    if url.startswith("http://"):
        url = "https://" + url[7:]
    try:
        result = page.evaluate(
            """async (url) => {
                try {
                    const resp = await fetch(url);
                    if (!resp.ok) {
                        return { ok: false, status: resp.status, error: `HTTP ${resp.status}` };
                    }
                    const buffer = await resp.arrayBuffer();
                    const bytes = new Uint8Array(buffer);
                    let binary = '';
                    const len = bytes.byteLength;
                    for (let i = 0; i < len; i++) {
                        binary += String.fromCharCode(bytes[i]);
                    }
                    return {
                        ok: true,
                        size: len,
                        base64: btoa(binary)
                    };
                } catch (e) {
                    return { ok: false, error: e.toString() };
                }
            }""",
            url
        )
        if result and result.get("ok") and result.get("size", 0) > 100:
            import base64
            data = base64.b64decode(result["base64"])
            if b"%PDF" in data[:10]:
                return data
            else:
                log_event(f"Dokumen bukan PDF (Header: {data[:10]!r}) dari {url}")
        elif result and not result.get("ok"):
            log_event(f"Gagal unduh file ({result.get('error', 'status not ok')}): {url}")
    except Exception as e:
        log_event(f"Download PDF error: {e}")
    return None

def analisis_dokumen(page, link: str, lampiran: list, judul: str) -> str:
    # Prioritaskan link lampiran berekstensi .pdf lebih dulu
    pdf_candidates = [u for u in lampiran if u and ".pdf" in u.lower()]
    other_candidates = [u for u in (lampiran + ([link] if link else [])) if u and u not in pdf_candidates]
    urls = pdf_candidates + other_candidates

    pdf_bytes = None
    for u in urls:
        if u:
            pdf_bytes = download_pdf(page, u)
            if pdf_bytes:
                break

    if not pdf_bytes:
        log_event("Tidak ada PDF yang bisa didownload.")
        return ""

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        log_event(f"Mengirim PDF ({len(pdf_bytes)//1024} KB) ke Gemini AI...")
        uploaded = ai_client.files.upload(file=tmp_path)
        
        try:
            response = ai_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    uploaded,
                    f"Judul dokumen: {judul}\n\n{AI_PROMPT}"
                ]
            )
        except Exception as e_model:
            log_event(f"Model {GEMINI_MODEL} notice ({e_model}), mencoba fallback ke gemini-1.5-flash...")
            response = ai_client.models.generate_content(
                model="gemini-1.5-flash",
                contents=[
                    uploaded,
                    f"Judul dokumen: {judul}\n\n{AI_PROMPT}"
                ]
            )
            
        insight = response.text.strip()
        log_event(f"Insight AI berhasil dibuat ({len(insight)} karakter)")
        return insight
    except Exception as e:
        err_str = str(e)
        if "401" in err_str or "UNAUTHENTICATED" in err_str or "ACCOUNT_STATE_INVALID" in err_str:
            log_event("⚠️ Gemini AI Error: API Key tidak valid atau Service Account dinonaktifkan di Google Cloud Console.")
        else:
            log_event(f"Gemini AI error: {e}")
        return ""
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

# ---------------------------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------------------------

def kirim_telegram(judul: str, link: str, emiten: str, siaran_pers: bool, lampiran: list, insight: str = "") -> bool:
    safe_judul = html.escape(judul)
    safe_emiten = html.escape(emiten)
    label = "📰 <b>Siaran Pers</b>\n" if siaran_pers else ""
    tag = f"<b>[{safe_emiten}]</b> " if safe_emiten else ""
    teks = f"{label}{tag}{safe_judul}\n\n🔗 <a href=\"{link}\">Dokumen Utama</a>"

    for i, lamp in enumerate(lampiran, start=1):
        teks += f'\n\n📎 <a href="{lamp}">Lampiran {i}</a>'

    if insight:
        teks += f"\n\n{insight}"

    teks += "\n\n<i>Menyaring Noise, Memberi Insight — PintarSaham</i>"

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": teks,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=20
        )
        return r.status_code == 200 and r.json().get("ok")
    except Exception as e:
        log_event(f"Telegram error: {e}")
        return False

# ---------------------------------------------------------------------------
# SCAN SATU PUTARAN (DIRECT KE IDX)
# ---------------------------------------------------------------------------

def wait_for_cards(page, max_retries: int = 2):
    """Menunggu elemen attach-card dengan deteksi Cloudflare dan auto-reload jika macet."""
    for attempt in range(max_retries):
        # Deteksi Cloudflare baik dari title maupun isi DOM
        for cf_attempt in range(25):
            t = (page.title() or "").lower()
            try:
                content = (page.content() or "").lower()[:2500]
            except Exception:
                content = ""

            is_cf = (
                any(w in t for w in ["just a moment", "tunggu", "attention required", "security check", "cloudflare"])
                or "cf-turnstile" in content
                or "challenge-platform" in content
            )

            if is_cf:
                log_event(f"Menunggu verifikasi Cloudflare ({cf_attempt + 1}/25)...")
                time.sleep(2)
            else:
                break

        try:
            page.wait_for_selector("div.attach-card", timeout=35000)
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                log_event(f"Elemen pengumuman belum muncul (percobaan {attempt + 1}/{max_retries}), me-refresh halaman...")
                try:
                    page.reload(wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    page.goto(URL, wait_until="domcontentloaded", timeout=45000)
                time.sleep(3)
            else:
                raise e
    return False

def scan_sekali(page) -> list:
    items = []
    try:
        page.goto(URL, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        log_event(f"Koneksi awal ke IDX lambat ({e}), mencoba muat ulang...")
        page.goto(URL, wait_until="domcontentloaded", timeout=45000)

    wait_for_cards(page, max_retries=2)

    for halaman in range(1, PAGES_TO_SCAN + 1):
        kartu = page.query_selector_all("div.attach-card")
        for c in kartu:
            judul_el = c.query_selector("h6.title a")
            time_el = c.query_selector("time")
            if not judul_el:
                continue

            judul_raw = judul_el.inner_text()
            link = judul_el.get_attribute("href") or ""
            if link and link.startswith("/"):
                link = f"https://www.idx.co.id{link}"
            judul = judul_bersih(judul_raw)

            if is_blacklisted(judul):
                continue

            pid = extract_id(c, link)
            if not pid:
                continue

            lampiran = extract_lampiran(c)
            items.append({
                "id": pid,
                "judul": judul,
                "link": link,
                "lampiran": lampiran,
                "emiten": extract_emiten(judul_raw),
                "siaran_pers": is_siaran_pers(judul),
                "waktu": normalize(time_el.inner_text()) if time_el else "",
            })

        if halaman < PAGES_TO_SCAN:
            nxt = page.query_selector("button.btn-arrow.--next")
            if not nxt or nxt.is_disabled():
                break
            first = page.query_selector("div.attach-card h6.title a")
            sig = first.inner_text() if first else ""
            nxt.click()
            try:
                page.wait_for_function(
                    """(prev) => {
                        const el = document.querySelector('div.attach-card h6.title a');
                        return el && el.innerText !== prev;
                    }""",
                    arg=sig,
                    timeout=15000
                )
            except Exception:
                break

    items.reverse()
    return items

# ---------------------------------------------------------------------------
# BACKGROUND WORKER LOOP (DIRECT CONNECTION)
# ---------------------------------------------------------------------------

def setup_page_optimizations(page):
    """Blokir download resource berat (gambar, media, font) agar hemat RAM dan bandwidth."""
    def block_unnecessary(route):
        if route.request.resource_type in ["image", "media", "font"]:
            try:
                route.abort()
            except Exception:
                pass
        else:
            try:
                route.continue_()
            except Exception:
                pass

    try:
        page.route("**/*", block_unnecessary)
    except Exception:
        pass

def run_worker():
    log_event(f"Memulai bot worker (Mode Direct, Headless={HEADLESS}, RAM Optimized)...")
    seen = load_seen()
    first_run = False

    launch_kwargs = {
        "headless": HEADLESS,
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-zygote",
            "--renderer-process-limit=1",
            "--blink-settings=imagesEnabled=false",
            "--js-flags=--max-old-space-size=128",
            "--disable-background-networking",
            "--disable-extensions",
            "--disable-default-apps",
            "--window-size=1280,800"
        ]
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="id-ID",
            timezone_id="Asia/Jakarta",
            viewport={"width": 1280, "height": 800}
        )
        context.set_default_navigation_timeout(35000)
        context.set_default_timeout(30000)

        page = context.new_page()
        Stealth().apply_stealth_sync(page)
        setup_page_optimizations(page)

        while True:
            stamp = datetime.now().strftime("%H:%M:%S")
            bot_status["state"] = "Sedang Memeriksa IDX..."
            bot_status["last_scan"] = stamp

            if page.is_closed():
                log_event("Tab browser sempat tertutup, membuat tab baru...")
                page = context.new_page()
                Stealth().apply_stealth_sync(page)
                setup_page_optimizations(page)

            try:
                items = scan_sekali(page)
            except Exception as e:
                log_event(f"⚠️ Gagal scan IDX ({e}). Akan dicoba lagi...")
                bot_status["state"] = f"Standby (Scan Terakhir Gagal: {stamp})"
                try:
                    page.goto("about:blank")
                except Exception:
                    pass
                gc.collect()
                time.sleep(POLL_INTERVAL * 60)
                continue

            baru = [it for it in items if it["id"] not in seen]
            log_event(f"{len(items)} lolos filter, {len(baru)} pengumuman baru.")

            for it in baru:
                if first_run:
                    seen.add(it["id"])
                    continue

                bot_status["state"] = f"Menganalisis [{it['emiten']}]..."
                insight = analisis_dokumen(page, it["link"], it["lampiran"], it["judul"])
                berhasil = kirim_telegram(it["judul"], it["link"], it["emiten"], it["siaran_pers"], it["lampiran"], insight)

                if berhasil:
                    seen.add(it["id"])
                    bot_status["total_sent"] += 1
                    bot_status["last_items"].append({
                        "emiten": it["emiten"],
                        "judul": it["judul"],
                        "time": stamp
                    })
                    if len(bot_status["last_items"]) > 10:
                        bot_status["last_items"].pop(0)
                    log_event(f"-> Terkirim: [{it['emiten'] or '-'}] {it['judul'][:50]}")
                    time.sleep(1)

            if first_run:
                log_event(f"{len(baru)} ID lama dipelajari. Siklus berikutnya akan mengirim notifikasi.")
                first_run = False

            save_seen(seen)
            bot_status["state"] = f"Standby (Cek berikutnya dalam {POLL_INTERVAL} mnt)"

            # OPTIMASI RAM: Lepas DOM halaman saat standby & panggil Garbage Collector
            try:
                page.goto("about:blank")
            except Exception:
                pass
            gc.collect()

            time.sleep(POLL_INTERVAL * 60)

# ---------------------------------------------------------------------------
# FASTAPI WEB DASHBOARD (http://localhost:7860)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    t = threading.Thread(target=run_worker, daemon=True)
    t.start()
    yield

app = FastAPI(title="IDX Telegram Bot Dashboard", lifespan=lifespan)

@app.get("/health")
def health():
    return {
        "status": "ok",
        "time": datetime.now().isoformat(),
        "total_sent": bot_status.get("total_sent", 0)
    }

@app.get("/", response_class=HTMLResponse)
def index():
    logs_html = "".join(f"<div class='log-line'>{html.escape(l)}</div>" for l in reversed(bot_logs))
    items_html = "".join(
        f"<li><b>[{html.escape(it.get('emiten', ''))}]</b> {html.escape(it.get('judul', ''))} <span class='badge'>{it.get('time', '')}</span></li>"
        for it in reversed(bot_status["last_items"])
    ) or "<li>Belum ada pengumuman terkirim.</li>"

    return f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="5">
    <title>IDX Notifier & AI Bot</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }}
        .container {{ max-width: 960px; margin: 0 auto; }}
        .header {{ display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #334155; padding-bottom: 15px; margin-bottom: 20px; }}
        .title {{ font-size: 24px; font-weight: bold; color: #38bdf8; }}
        .status-card {{ background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 20px; border: 1px solid #334155; display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }}
        .stat-item {{ background: #0f172a; padding: 15px; border-radius: 8px; }}
        .stat-label {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; margin-bottom: 5px; }}
        .stat-value {{ font-size: 17px; font-weight: bold; color: #38bdf8; word-break: break-all; }}
        .card {{ background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 20px; border: 1px solid #334155; }}
        .logs {{ background: #020617; border-radius: 8px; padding: 15px; font-family: monospace; font-size: 13px; max-height: 280px; overflow-y: auto; color: #a5f3fc; }}
        .log-line {{ margin-bottom: 4px; }}
        ul {{ padding-left: 20px; color: #cbd5e1; }}
        li {{ margin-bottom: 8px; }}
        .badge {{ background: #0284c7; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px; margin-left: 8px; }}
        .refresh-hint {{ font-size: 12px; color: #64748b; text-align: right; margin-top: 5px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="title">📈 IDX Notifier & AI Bot</div>
            <div><span style="color: #4ade80;">● Online (Direct Connection)</span></div>
        </div>

        <div class="status-card">
            <div class="stat-item">
                <div class="stat-label">Status Bot</div>
                <div class="stat-value" style="color: #4ade80;">{bot_status['state']}</div>
            </div>
            <div class="stat-item">
                <div class="stat-label">Terakhir Scan</div>
                <div class="stat-value">{bot_status['last_scan']}</div>
            </div>
            <div class="stat-item">
                <div class="stat-label">Total Terkirim</div>
                <div class="stat-value">{bot_status['total_sent']} Dokumen</div>
            </div>
            <div class="stat-item">
                <div class="stat-label">Koneksi</div>
                <div class="stat-value" style="color: #4ade80; font-size: 15px;">Direct (Tanpa Proxy)</div>
            </div>
        </div>

        <div class="card">
            <h3>📢 Pengumuman Terakhir yang Dikirim</h3>
            <ul>{items_html}</ul>
        </div>

        <div class="card">
            <h3>📜 Aktivitas Real-Time</h3>
            <div class="logs">{logs_html}</div>
            <div class="refresh-hint">Halaman otomatis refresh tiap 5 detik</div>
        </div>
    </div>
</body>
</html>"""

if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
