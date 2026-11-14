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
import logging
from google import genai
from google.genai import types
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

# Redam warning AFC yang tidak perlu dari SDK google-genai
logging.getLogger("google.genai").setLevel(logging.ERROR)

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
TELEGRAM_THREAD_ID = os.getenv("TELEGRAM_THREAD_ID", os.getenv("TELEGRAM_TOPIC_ID", "")).strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash").strip()
DEFAULT_FALLBACK_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.6-flash",
]
raw_fallbacks = os.getenv("GEMINI_FALLBACK_MODELS", "").strip()
if raw_fallbacks:
    FALLBACK_MODELS = [m.strip() for m in raw_fallbacks.split(",") if m.strip()]
else:
    FALLBACK_MODELS = [m for m in DEFAULT_FALLBACK_MODELS]

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
    "last_items": [],
    "active_model": GEMINI_MODEL
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

BLACKLIST_EMITEN = ["IDX", "ZP", "BK"]

def is_blacklisted(judul: str, emiten: str = "") -> bool:
    e = (emiten or "").strip().upper()
    # 1. Filter langsung jika pengumuman dari bursa [IDX] atau broker [ZP], [BK]
    if e in BLACKLIST_EMITEN:
        return True

    j = normalize(judul)

    # 2. Cek prefix blacklist umum
    if any(j.startswith(prefix) for prefix in BLACKLIST_PREFIXES):
        return True

    # 3. Filter pengumuman bursa [IDX] terkait ZP (Maybank) & BK (JP Morgan) Waran Terstruktur
    is_zp = bool(re.search(r"\bzp\b|maybank", j))
    is_bk = bool(re.search(r"\bbk\b|j\.?p\.?\s*morgan", j))
    is_waran_securities = any(w in j for w in ["waran", "warrant", "structured", "terstruktur", "sekuritas"])

    if (e == "IDX" or not e) and (is_zp or is_bk) and is_waran_securities:
        return True

    if "maybank sekuritas" in j or re.search(r"j\.?p\.?\s*morgan\s+sekuritas", j):
        return True

    if e == "IDX" and ("waran terstruktur" in j or "structured warrant" in j):
        return True

    return False

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

try:
    ai_client = genai.Client(
        api_key=API_AI,
        http_options=types.HttpOptions(
            timeout=45000,
            retry_options=types.HttpRetryOptions(attempts=2, initial_delay=1.0, max_delay=4.0)
        )
    )
except Exception as e_client:
    print(f"⚠️ Notice inisialisasi genai.Client options: {e_client}")
    ai_client = genai.Client(api_key=API_AI)

AI_PROMPT = """Kamu adalah analis pasar modal Indonesia yang ahli.

Analisis dokumen keterbukaan informasi IDX berikut dan berikan ringkasan serta sentimen secara singkat dan terstruktur.

Format jawaban (WAJIB IKUTI DENGAN PERSIS):

💡 <b>Ringkasan:</b>
[Uraian singkat inti dokumen dan dampaknya bagi investor dalam 2-3 kalimat]

<b>Sentimen:</b> Positif 🟢 / Netral 🟡 / Negatif 🔴

Aturan:
- Gunakan bahasa Indonesia yang ringkas, bernas, dan mudah dipahami
- Fokus pada dampak MATERIAL bagi investor/pemegang saham
- Jangan mengulang judul dokumen
- Selalu pisahkan baris Sentimen dengan satu baris kosong (enter) di bawah ringkasan
- JANGAN gunakan format markdown (** atau ##), gunakan tag HTML (<b>, <i>) saja
- Maksimal 300 karakter"""

def download_pdf(page, url: str) -> bytes:
    if not url:
        return None
    if url.startswith("http://"):
        url = "https://" + url[7:]

    # Cara 1 (Utama): Unduh via tab browser Playwright (Native Chromium Download)
    # Memiliki TLS fingerprint Chrome asli & mewarisi cookie clearance Cloudflare,
    # sehingga kebal dari pemblokiran Cloudflare WAF CORS/AJAX HTTP 403.
    dl_page = None
    try:
        dl_page = page.context.new_page()
        Stealth().apply_stealth_sync(dl_page)

        try:
            with dl_page.expect_download(timeout=25000) as dl_info:
                try:
                    resp = dl_page.goto(url, timeout=25000)
                    if resp and resp.ok:
                        data = resp.body()
                        if len(data) > 100 and b"%PDF" in data[:10]:
                            return data
                except Exception as e_goto:
                    err_msg = str(e_goto).lower()
                    if "download is starting" not in err_msg and "err_aborted" not in err_msg:
                        # Jika tertahan di challenge Cloudflare pada tab baru, coba klik verifikasi otomatis
                        for _ in range(8):
                            t = (dl_page.title() or "").lower()
                            if any(w in t for w in ["just a moment", "tunggu", "cloudflare", "security check"]):
                                for frame in dl_page.frames:
                                    try:
                                        cb = frame.query_selector("input[type='checkbox'], span.ctp-checkbox-label, div.ctp-checkbox")
                                        if cb:
                                            cb.click()
                                            break
                                        fe = frame.frame_element()
                                        box = fe.bounding_box()
                                        if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                                            dl_page.mouse.click(box["x"] + 25, box["y"] + box["height"] / 2)
                                            break
                                    except Exception:
                                        pass
                                time.sleep(2)
                            else:
                                break

            download = dl_info.value
            tmp_path = download.path()
            if tmp_path and os.path.exists(tmp_path):
                with open(tmp_path, "rb") as f:
                    data = f.read()
                if len(data) > 100 and b"%PDF" in data[:10]:
                    return data
                elif len(data) > 100:
                    log_event(f"Dokumen bukan PDF (Header: {data[:10]!r}) dari {url}")
                    return None
        except Exception as e_dl:
            log_event(f"Browser download notice ({e_dl}): {url}")
    except Exception as e:
        log_event(f"Download tab creation error: {e}")
    finally:
        if dl_page:
            try:
                dl_page.close()
            except Exception:
                pass

    # Cara 2 (Fallback): Gunakan Playwright Request Context
    try:
        resp = page.context.request.get(
            url,
            headers={
                "Referer": page.url or URL,
                "Accept": "application/pdf,*/*",
            },
            timeout=15000
        )
        if resp.ok:
            data = resp.body()
            if len(data) > 100 and b"%PDF" in data[:10]:
                return data
            elif len(data) > 100:
                log_event(f"Dokumen bukan PDF (Header: {data[:10]!r}) dari {url}")
                return None
        elif resp.status != 403:
            log_event(f"Gagal unduh file context.request (HTTP {resp.status}): {url}")
    except Exception as e:
        pass

    # Cara 3 (Fallback): Evaluate di dalam halaman
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
        log_event(f"Download PDF fallback error: {e}")
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

    if not pdf_bytes or len(pdf_bytes) < 100:
        log_event("Tidak ada PDF valid yang bisa didownload.")
        return ""

    if len(pdf_bytes) > 25 * 1024 * 1024:
        log_event(f"Ukuran PDF terlalu besar ({len(pdf_bytes)//(1024*1024)} MB), melewati analisis AI.")
        return ""

    tmp_path = ""
    uploaded = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        # Upload ke Gemini Files API dengan retry 1x
        for up_try in range(2):
            try:
                log_event(f"Mengirim PDF ({len(pdf_bytes)//1024} KB) ke Gemini AI...")
                uploaded = ai_client.files.upload(file=tmp_path)
                break
            except Exception as e_up:
                if up_try == 0:
                    time.sleep(2)
                else:
                    log_event(f"⚠️ Gagal upload PDF ke Gemini: {e_up}")
                    return ""

        if not uploaded:
            log_event("⚠️ File PDF tidak berhasil disiapkan di Gemini.")
            return ""

        # Siapkan rantai model: model utama diikuti model cadangan yang valid
        candidate_models = [GEMINI_MODEL]
        for fb in FALLBACK_MODELS:
            if fb not in candidate_models:
                candidate_models.append(fb)

        response = None
        last_error = None
        model_used = None

        gen_config = types.GenerateContentConfig(
            temperature=0.2,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        )

        for model_name in candidate_models:
            for attempt in range(2):
                try:
                    resp = ai_client.models.generate_content(
                        model=model_name,
                        contents=[
                            uploaded,
                            f"Judul dokumen: {judul}\n\n{AI_PROMPT}"
                        ],
                        config=gen_config
                    )
                    if resp:
                        response = resp
                        model_used = model_name
                        bot_status["active_model"] = model_name
                        break
                except Exception as e_req:
                    last_error = e_req
                    err_text = str(e_req)

                    # 1. Kuota / Rate Limit (429)
                    if any(c in err_text for c in ["429", "RESOURCE_EXHAUSTED"]):
                        m_delay = re.search(r"retry\s*(?:in|delay:?)\s*['\"]?(\d+)", err_text, re.IGNORECASE)
                        wait_sec = (int(m_delay.group(1)) + 1) if m_delay else 10
                        if wait_sec <= 12 and attempt == 0:
                            log_event(f"Model {model_name} rate limit (429), jeda {wait_sec}s lalu coba lagi ({attempt+1}/2)...")
                            time.sleep(wait_sec)
                            continue
                        else:
                            log_event(f"Model {model_name} kuota penuh (429), beralih ke model cadangan...")
                            break

                    # 2. Server Padat / Unavailable (503)
                    elif any(c in err_text for c in ["503", "UNAVAILABLE"]):
                        if attempt == 0:
                            log_event(f"Model {model_name} padat (503), mencoba ulang dalam 2 detik ({attempt+1}/2)...")
                            time.sleep(2)
                            continue
                        else:
                            log_event(f"Model {model_name} padat (503), beralih ke model cadangan...")
                            break

                    # 3. Model Tidak Ditemukan / Deprecated (404)
                    elif any(c in err_text for c in ["404", "NOT_FOUND", "no longer available"]):
                        log_event(f"Model {model_name} tidak tersedia (404), beralih ke model cadangan...")
                        break

                    # 4. Auth / API Key Error (Fatal)
                    elif any(c in err_text for c in ["401", "UNAUTHENTICATED", "ACCOUNT_STATE_INVALID"]):
                        log_event("⚠️ Gemini AI Error: API Key tidak valid atau Service Account dinonaktifkan di Google Cloud Console.")
                        return ""

                    # Error lainnya
                    else:
                        log_event(f"Model {model_name} notice: {e_req}")
                        break

            if response:
                break

        if not response:
            raise last_error or Exception("Semua model Gemini gagal merespons.")

        # Ekstraksi insight dengan penanganan blokir safety filter
        insight = ""
        try:
            if hasattr(response, "text") and response.text:
                insight = response.text.strip()
            elif hasattr(response, "candidates") and response.candidates:
                cand = response.candidates[0]
                if hasattr(cand, "content") and cand.content and hasattr(cand.content, "parts"):
                    insight = "".join([p.text for p in cand.content.parts if hasattr(p, "text") and p.text]).strip()
                if not insight and hasattr(cand, "finish_reason"):
                    log_event(f"Notice Gemini finish reason: {cand.finish_reason}")
        except Exception as e_parse:
            log_event(f"Notice ekstraksi teks Gemini: {e_parse}")

        if insight:
            log_event(f"Insight AI berhasil dibuat menggunakan {model_used} ({len(insight)} karakter)")
        else:
            log_event(f"Gemini {model_used} tidak menghasilkan teks (kemungkinan filter/format).")
        return insight

    except Exception as e:
        err_str = str(e)
        if "401" in err_str or "UNAUTHENTICATED" in err_str or "ACCOUNT_STATE_INVALID" in err_str:
            log_event("⚠️ Gemini AI Error: API Key tidak valid atau Service Account dinonaktifkan di Google Cloud Console.")
        else:
            log_event(f"Gemini AI error: {e}")
        return ""
    finally:
        # Bersihkan file remote di Google Gemini Files API
        if uploaded and hasattr(uploaded, "name") and uploaded.name:
            try:
                ai_client.files.delete(name=uploaded.name)
            except Exception:
                pass
        # Bersihkan file temporary lokal
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

# ---------------------------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------------------------

def format_insight(insight: str) -> str:
    """Format teks insight: ganti header ke '💡 Ringkasan:' dan pastikan 'Sentimen:' selalu di-enter di baris baru."""
    if not insight:
        return ""
    text = insight.strip()

    # Ganti header lama jika masih dihasilkan model
    text = re.sub(r"(?:📊\s*)?<b>\s*Insight AI\s*:?\s*</b>", "💡 <b>Ringkasan:</b>", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:📊\s*)?Insight AI\s*:?", "💡 <b>Ringkasan:</b>", text, flags=re.IGNORECASE)

    # Pastikan label Sentimen selalu dipisah baris baru (enter ganda)
    text = re.sub(r"([^\n])\s*(<b>\s*Sentimen\s*:?\s*</b>)", r"\1\n\n\2", text, flags=re.IGNORECASE)
    text = re.sub(r"([^\n])\s*(Sentimen\s*:)", r"\1\n\n<b>Sentimen:</b>", text, flags=re.IGNORECASE)

    # Bersihkan newline berlebih
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text

def kirim_telegram(judul: str, link: str, emiten: str, siaran_pers: bool, lampiran: list, insight: str = "") -> bool:
    safe_judul = html.escape(judul)
    safe_emiten = html.escape(emiten)
    label = "📰 <b>Siaran Pers</b>\n" if siaran_pers else ""
    tag = f"<b>[{safe_emiten}]</b> " if safe_emiten else ""

    bagian = [f"{label}{tag}{safe_judul}"]

    # Ringkasan insight AI (dengan sentimen terpisah enter)
    cleaned_insight = format_insight(insight)
    if cleaned_insight:
        bagian.append(cleaned_insight)

    # Dokumen Utama & Lampiran diposisikan di bawah ringkasan
    links = []
    if link:
        links.append(f'🔗 <a href="{link}">Dokumen Utama</a>')
    for i, lamp in enumerate(lampiran, start=1):
        links.append(f'📎 <a href="{lamp}">Lampiran {i}</a>')

    if links:
        bagian.append("\n".join(links))

    bagian.append("<i>Menyaring Noise, Memberi Insight — PintarSaham</i>")
    teks = "\n\n".join(bagian)

    chat_targets = [c.strip() for c in TELEGRAM_CHAT_ID.split(",") if c.strip()]
    if not chat_targets:
        log_event("Telegram error: TELEGRAM_CHAT_ID kosong.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    any_success = False

    for target in chat_targets:
        # Dukung format chat_id:thread_id atau chat_id/thread_id
        if ":" in target:
            cid, tid = target.split(":", 1)
        elif "/" in target:
            cid, tid = target.split("/", 1)
        else:
            cid = target
            tid = TELEGRAM_THREAD_ID

        cid = cid.strip()
        tid = str(tid).strip() if tid else ""

        payload = {
            "chat_id": cid,
            "text": teks,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        if tid:
            try:
                payload["message_thread_id"] = int(tid)
            except ValueError:
                log_event(f"Notice: thread_id '{tid}' bukan angka valid, diabaikan.")

        try:
            r = requests.post(
                url,
                data=payload,
                timeout=20
            )
            if r.status_code == 200 and r.json().get("ok"):
                any_success = True
            else:
                err_text = r.text
                # Jika error karena parsing HTML (can't parse entities / unclosed tags), fallback ke teks biasa
                if ("can't parse entities" in err_text.lower() or "bad request: can't parse" in err_text.lower()) and payload.get("parse_mode"):
                    log_event(f"Telegram parse HTML gagal ({err_text[:70]}). Mengirim ulang sebagai teks biasa...")
                    payload_plain = dict(payload)
                    payload_plain.pop("parse_mode", None)
                    payload_plain["text"] = re.sub(r"<[^>]+>", "", teks)
                    r_plain = requests.post(url, data=payload_plain, timeout=20)
                    if r_plain.status_code == 200 and r_plain.json().get("ok"):
                        any_success = True
                        log_event(f"-> Terkirim sebagai teks biasa ke {cid}")
                        continue

                # Jika topik berstatus ditutup (CLOSED), bot buka sebentar, kirim, lalu kunci lagi
                if "TOPIC_CLOSED" in err_text and tid:
                    log_event(f"Topik {tid} tertutup. Membuka topik sementara untuk mengirim...")
                    reopen_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/reopenForumTopic"
                    close_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/closeForumTopic"
                    
                    try:
                        reopen_res = requests.post(reopen_url, data={"chat_id": cid, "message_thread_id": int(tid)}, timeout=10)
                        if reopen_res.status_code == 200 and reopen_res.json().get("ok"):
                            # Kirim pesan saat topik terbuka
                            r_retry = requests.post(url, data=payload, timeout=20)
                            if r_retry.status_code == 200 and r_retry.json().get("ok"):
                                any_success = True
                                log_event(f"-> Pesan sukses terkirim ke topik {tid}!")
                            else:
                                log_event(f"Gagal kirim setelah topik dibuka: {r_retry.text}")
                            
                            # Kunci kembali topiknya
                            time.sleep(1)
                            close_res = requests.post(close_url, data={"chat_id": cid, "message_thread_id": int(tid)}, timeout=10)
                            if close_res.status_code == 200 and close_res.json().get("ok"):
                                log_event(f"Topik {tid} berhasil dikunci/ditutup kembali.")
                            else:
                                log_event(f"Gagal mengunci kembali topik {tid}: {close_res.text}")
                        else:
                            log_event(f"Gagal auto-reopen topik {tid}. Pastikan bot diberi izin admin 'Manage Topics' (Kelola Topik). Detail: {reopen_res.text}")
                    except Exception as e_topic:
                        log_event(f"Error proses auto-reopen/close: {e_topic}")
                else:
                    log_event(f"Telegram kirim ke {cid} (thread {tid or 'main'}) gagal: {err_text}")
        except Exception as e:
            log_event(f"Telegram error ke {cid} (thread {tid or 'main'}): {e}")

    return any_success

# ---------------------------------------------------------------------------
# SCAN SATU PUTARAN (DIRECT KE IDX)
# ---------------------------------------------------------------------------

def wait_for_cards(page, max_retries: int = 2):
    """Menunggu elemen attach-card dengan deteksi Cloudflare dan auto-reload jika macet."""
    for attempt in range(max_retries):
        t_cur = ""
        u_cur = ""
        try:
            t_cur = page.title() or ""
            u_cur = page.url or ""
        except Exception:
            pass
        log_event(f"Status Halaman (percobaan {attempt + 1}/{max_retries}): '{t_cur}' ({u_cur})")

        # Deteksi Cloudflare baik dari title maupun isi DOM
        for cf_attempt in range(30):
            try:
                t = (page.title() or "").lower()
            except Exception:
                t = ""
            try:
                content = (page.content() or "").lower()[:3000]
            except Exception:
                content = ""

            is_cf = (
                any(w in t for w in ["just a moment", "tunggu", "attention required", "security check", "cloudflare", "access denied", "forbidden"])
                or any(w in content for w in ["cf-turnstile", "challenge-platform", "cf-wrapper", "ray id", "checking your browser"])
            )

            if is_cf:
                log_event(f"Menunggu verifikasi Cloudflare ({cf_attempt + 1}/30)...")
                try:
                    for frame in page.frames:
                        if any(k in frame.url.lower() for k in ["cloudflare", "turnstile", "challenge"]):
                            cb = frame.query_selector("input[type='checkbox'], span.ctp-checkbox-label, div.ctp-checkbox")
                            if cb:
                                cb.click()
                                time.sleep(1)
                                break
                            fe = frame.frame_element()
                            box = fe.bounding_box()
                            if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                                page.mouse.click(box["x"] + 25, box["y"] + box["height"] / 2)
                                time.sleep(1)
                                break
                except Exception:
                    pass
                time.sleep(2)
            else:
                break

        try:
            try:
                page.wait_for_selector("div.attach-card", timeout=25000)
                return True
            except Exception:
                # Picu scroll ke bawah untuk memicu lazy-load / Vue render
                try:
                    page.evaluate("() => window.scrollTo(0, 500)")
                except Exception:
                    pass
                page.wait_for_selector("div.attach-card", timeout=20000)
                return True
        except Exception as e:
            try:
                body_preview = page.evaluate("() => document.body ? document.body.innerText.slice(0, 300) : ''")
                body_clean = " ".join((body_preview or "").split())
                log_event(f"Cuplikan layar saat timeout: {body_clean[:200]!r}")
            except Exception:
                pass

            if attempt < max_retries - 1:
                log_event(f"Elemen pengumuman belum muncul (percobaan {attempt + 1}/{max_retries}), me-refresh halaman...")
                try:
                    page.reload(wait_until="domcontentloaded", timeout=35000)
                except Exception:
                    page.goto(URL, wait_until="domcontentloaded", timeout=35000)
                time.sleep(3)
            else:
                raise e
    return False

def scan_sekali(page) -> list:
    items = []
    try:
        page.goto(URL, wait_until="domcontentloaded", timeout=35000)
    except Exception as e:
        log_event(f"Koneksi awal ke IDX lambat/notice ({e}), memeriksa konten...")

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
            emiten = extract_emiten(judul_raw)

            if is_blacklisted(judul, emiten):
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
                "emiten": emiten,
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
    """Optimasi halaman tanpa mencegat route agar tidak membebani koneksi API IDX."""
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
            "--disable-blink-features=AutomationControlled",
            "--renderer-process-limit=1",
            "--js-flags=--max-old-space-size=384",
            "--disable-background-networking",
            "--disable-extensions",
            "--disable-default-apps",
            "--window-size=1280,800"
        ]
    }

    while True:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(**launch_kwargs)
                try:
                    context = browser.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                        locale="id-ID",
                        timezone_id="Asia/Jakarta",
                        viewport={"width": 1280, "height": 800},
                        accept_downloads=True
                    )
                    context.set_default_navigation_timeout(35000)
                    context.set_default_timeout(30000)

                    page = context.new_page()
                    Stealth().apply_stealth_sync(page)
                    setup_page_optimizations(page)

                    while True:
                        if not browser.is_connected() or page.is_closed():
                            log_event("Sesi browser terputus, memulai ulang browser...")
                            break

                        stamp = datetime.now().strftime("%H:%M:%S")
                        bot_status["state"] = "Sedang Memeriksa IDX..."
                        bot_status["last_scan"] = stamp

                        try:
                            items = scan_sekali(page)
                        except Exception as e:
                            err_str = str(e)
                            log_event(f"⚠️ Gagal scan IDX ({err_str}). Akan dicoba lagi...")
                            bot_status["state"] = f"Standby (Scan Terakhir Gagal: {stamp})"

                            is_fatal = any(k in err_str.lower() for k in [
                                "crashed", "crash", "target closed", "connection closed", "session closed", "browser has been closed"
                            ])
                            if is_fatal or not browser.is_connected() or page.is_closed():
                                log_event("🔄 Browser tab/proses crash atau sesi terputus. Me-restart sesi browser...")
                                try:
                                    context.close()
                                except Exception:
                                    pass
                                try:
                                    browser.close()
                                except Exception:
                                    pass
                                time.sleep(10)
                                break

                            try:
                                page.goto("about:blank", timeout=10000)
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
                                time.sleep(4)

                        if first_run:
                            log_event(f"{len(baru)} ID lama dipelajari. Siklus berikutnya akan mengirim notifikasi.")
                            first_run = False

                        save_seen(seen)
                        bot_status["state"] = f"Standby (Cek berikutnya dalam {POLL_INTERVAL} mnt)"

                        # OPTIMASI RAM: Lepas DOM halaman saat standby & panggil Garbage Collector
                        try:
                            page.goto("about:blank", timeout=10000)
                        except Exception:
                            pass
                        gc.collect()

                        time.sleep(POLL_INTERVAL * 60)
                finally:
                    try:
                        browser.close()
                    except Exception:
                        pass
        except Exception as e_proc:
            log_event(f"Notice browser ({e_proc}), memulihkan otomatis dalam 10 detik...")
            time.sleep(10)

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

@app.get("/api/dashboard-data")
def dashboard_data():
    return {
        "state": bot_status.get("state", "-"),
        "last_scan": bot_status.get("last_scan", "-"),
        "total_sent": bot_status.get("total_sent", 0),
        "last_items": list(bot_status.get("last_items", [])),
        "logs": list(bot_logs)[-70:],
        "time": datetime.now().strftime("%H:%M:%S WIB"),
        "poll_interval": POLL_INTERVAL,
        "model": bot_status.get("active_model", GEMINI_MODEL)
    }

@app.get("/", response_class=HTMLResponse)
def index():
    return f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>IDX Sentinel — Intelligence Feed</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-app: #09090b;
            --bg-surface: #121215;
            --bg-subtle: #18181b;
            --bg-input: #151518;
            --border: rgba(255, 255, 255, 0.08);
            --border-focus: rgba(255, 255, 255, 0.22);
            --text-primary: #fafafa;
            --text-secondary: #a1a1aa;
            --text-muted: #71717a;
            --accent-emerald: #10b981;
            --accent-indigo: #6366f1;
            --accent-amber: #f59e0b;
            --accent-rose: #f43f5e;
            --radius-sm: 8px;
            --radius-md: 12px;
            --radius-lg: 16px;
            --font-sans: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            --font-mono: 'JetBrains Mono', monospace;
        }}

        *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
        html {{ font-size: 14px; -webkit-text-size-adjust: 100%; }}
        body {{
            font-family: var(--font-sans);
            background: var(--bg-app);
            color: var(--text-primary);
            line-height: 1.5;
            padding: 16px;
            min-height: 100vh;
            letter-spacing: -0.01em;
        }}

        /* Ambient Glow */
        body::before {{
            content: "";
            position: fixed;
            top: 0; left: 50%;
            transform: translateX(-50%);
            width: 100%; max-width: 900px; height: 260px;
            background: radial-gradient(50% 100% at 50% 0%, rgba(99, 102, 241, 0.07) 0%, transparent 100%);
            pointer-events: none; z-index: 0;
        }}

        .container {{
            max-width: 1100px;
            margin: 0 auto;
            position: relative;
            z-index: 1;
        }}

        /* TOPBAR */
        .topbar {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 12px;
            padding: 12px 18px;
            background: rgba(18, 18, 21, 0.85);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            margin-bottom: 20px;
        }}

        .brand {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        .brand-icon {{
            width: 32px; height: 32px;
            border-radius: var(--radius-sm);
            background: var(--text-primary);
            color: var(--bg-app);
            display: flex; align-items: center; justify-content: center;
        }}

        .brand-meta {{ display: flex; flex-direction: column; }}
        .brand-title {{ font-size: 15px; font-weight: 700; letter-spacing: -0.02em; }}
        .brand-sub {{ font-size: 11px; color: var(--text-muted); }}

        .topbar-right {{
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }}

        .status-pill {{
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            background: var(--bg-subtle);
            border: 1px solid var(--border);
            border-radius: 99px;
            font-size: 11px;
            font-family: var(--font-mono);
            color: var(--text-secondary);
        }}

        .pulse-dot {{
            width: 6px; height: 6px;
            border-radius: 50%;
            background: var(--accent-emerald);
            box-shadow: 0 0 8px var(--accent-emerald);
            animation: pulse 2s infinite;
        }}

        @keyframes pulse {{
            0% {{ transform: scale(0.95); opacity: 0.7; }}
            50% {{ transform: scale(1.3); opacity: 1; }}
            100% {{ transform: scale(0.95); opacity: 0.7; }}
        }}

        .clock-badge {{
            font-family: var(--font-mono);
            font-size: 11px;
            color: var(--text-muted);
            padding: 4px 8px;
            background: var(--bg-input);
            border: 1px solid var(--border);
            border-radius: 6px;
        }}

        .portal-link {{
            font-size: 11px;
            color: var(--text-muted);
            text-decoration: none;
            padding: 4px 8px;
            border: 1px solid var(--border);
            border-radius: 6px;
            transition: 0.15s;
        }}
        .portal-link:hover {{ color: var(--text-primary); border-color: var(--border-focus); }}

        /* STATS MATRIX BENTO */
        .stats-matrix {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 12px;
            margin-bottom: 20px;
        }}

        .stat-card {{
            background: var(--bg-surface);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}

        .stat-card .label {{
            font-size: 10px;
            font-family: var(--font-mono);
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--text-muted);
        }}

        .stat-card .value {{
            font-size: 18px;
            font-weight: 700;
            font-family: var(--font-mono);
            color: var(--text-primary);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        .stat-card .desc {{
            font-size: 11px;
            color: var(--text-muted);
            margin-top: 2px;
        }}

        /* MAIN CONTENT SPLIT (FEED & CONSOLE) */
        .main-split {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
        }}

        .panel {{
            background: var(--bg-surface);
            border: 1px solid var(--border);
            border-radius: var(--radius-lg);
            padding: 18px;
            display: flex;
            flex-direction: column;
        }}

        .panel-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding-bottom: 12px;
            margin-bottom: 14px;
            border-bottom: 1px solid var(--border);
        }}

        .panel-title {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 13px;
            font-weight: 600;
            color: var(--text-primary);
            letter-spacing: -0.01em;
        }}

        .panel-action-btn {{
            background: var(--bg-subtle);
            border: 1px solid var(--border);
            color: var(--text-secondary);
            font-size: 11px;
            padding: 3px 8px;
            border-radius: 6px;
            cursor: pointer;
            transition: 0.15s;
        }}
        .panel-action-btn:hover {{ color: var(--text-primary); border-color: var(--border-focus); }}

        /* ITEMS FEED */
        .feed-list {{
            display: flex;
            flex-direction: column;
            gap: 8px;
            max-height: 400px;
            overflow-y: auto;
            padding-right: 4px;
        }}

        .feed-item {{
            display: flex;
            align-items: flex-start;
            gap: 10px;
            padding: 10px 12px;
            background: var(--bg-subtle);
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            transition: 0.15s;
        }}

        .feed-item:hover {{
            border-color: var(--border-focus);
            transform: translateY(-1px);
        }}

        .ticker-tag {{
            font-family: var(--font-mono);
            font-size: 11px;
            font-weight: 700;
            background: rgba(99, 102, 241, 0.15);
            color: var(--accent-indigo);
            padding: 2px 6px;
            border-radius: 4px;
            flex-shrink: 0;
        }}

        .feed-info {{
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}

        .feed-title {{
            font-size: 12px;
            font-weight: 500;
            color: var(--text-primary);
            line-height: 1.4;
        }}

        .feed-time {{
            font-size: 10px;
            font-family: var(--font-mono);
            color: var(--text-muted);
        }}

        .feed-empty {{
            text-align: center;
            padding: 40px 16px;
            color: var(--text-muted);
            font-size: 12px;
        }}

        /* TERMINAL CONSOLE */
        .terminal-box {{
            background: #060608;
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            padding: 12px 14px;
            font-family: var(--font-mono);
            font-size: 11px;
            line-height: 1.6;
            color: var(--text-secondary);
            height: 400px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-word;
        }}

        .terminal-box::-webkit-scrollbar {{ width: 5px; }}
        .terminal-box::-webkit-scrollbar-thumb {{ background: rgba(255,255,255,0.12); border-radius: 99px; }}

        .log-row {{ margin-bottom: 3px; }}
        .log-success {{ color: var(--accent-emerald); }}
        .log-warn {{ color: var(--accent-amber); }}
        .log-danger {{ color: var(--accent-rose); }}
        .log-ai {{ color: #a5b4fc; }}
        .log-idx {{ color: #38bdf8; }}

        /* FOOTER */
        .page-footer {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px 0;
            margin-top: 16px;
            border-top: 1px solid var(--border);
            font-size: 11px;
            color: var(--text-muted);
        }}

        .page-footer code {{
            color: var(--text-primary);
            font-family: var(--font-mono);
        }}

        /* RESPONSIVE MOBILE */
        @media (max-width: 820px) {{
            body {{ padding: 10px; }}
            .stats-matrix {{ grid-template-columns: repeat(2, 1fr); }}
            .main-split {{ grid-template-columns: 1fr; }}
            .terminal-box {{ height: 280px; }}
            .topbar-right {{ justify-content: space-between; width: 100%; margin-top: 6px; }}
            .page-footer {{ flex-direction: column; gap: 6px; text-align: center; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- TOPBAR -->
        <header class="topbar">
            <div class="brand">
                <div class="brand-icon">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/>
                        <polyline points="16 7 22 7 22 13"/>
                    </svg>
                </div>
                <div class="brand-meta">
                    <span class="brand-title">IDX Sentinel</span>
                    <span class="brand-sub">Keterbukaan Informasi & AI Synthesis</span>
                </div>
            </div>

            <div class="topbar-right">
                <div class="status-pill">
                    <span class="pulse-dot"></span>
                    <span id="uiStateText">Connecting...</span>
                </div>
                <span class="clock-badge" id="uiClock">--:--:-- WIB</span>
                <a href="http://202.10.47.34:8000" class="portal-link" target="_blank" title="Buka Console Nexus">Nexus Console &rarr;</a>
            </div>
        </header>

        <!-- STATS MATRIX -->
        <div class="stats-matrix">
            <div class="stat-card">
                <span class="label">STATUS WORKER</span>
                <span class="value" id="uiWorkerState" style="color: var(--accent-emerald); font-size: 15px;">Standby</span>
                <span class="desc">Interval {POLL_INTERVAL} Menit</span>
            </div>
            <div class="stat-card">
                <span class="label">DOKUMEN TERKIRIM</span>
                <span class="value" id="uiTotalSent">0</span>
                <span class="desc">Notifikasi Telegram</span>
            </div>
            <div class="stat-card">
                <span class="label">TERAKHIR SCAN</span>
                <span class="value" id="uiLastScan">--:--:--</span>
                <span class="desc">Waktu Siklus Terakhir</span>
            </div>
            <div class="stat-card">
                <span class="label">AI ENGINE</span>
                <span class="value" id="uiAiModel" style="font-size: 14px; color: #a5b4fc;">{bot_status.get('active_model', GEMINI_MODEL)}</span>
                <span class="desc">Playwright Direct</span>
            </div>
        </div>

        <!-- MAIN CONTENT (FEED + LOG CONSOLE) -->
        <div class="main-split">
            <!-- Feed Panel -->
            <div class="panel">
                <div class="panel-header">
                    <div class="panel-title">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>
                        <span>Pengumuman Terakhir Terkirim</span>
                    </div>
                    <span class="badge-subtle" id="uiItemsCount">0 Item</span>
                </div>
                <div class="feed-list" id="uiFeedList">
                    <div class="feed-empty">Menunggu pengumuman baru dari Bursa Efek Indonesia...</div>
                </div>
            </div>

            <!-- Terminal Panel -->
            <div class="panel">
                <div class="panel-header">
                    <div class="panel-title">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
                        <span>Konsol Aktivitas Worker</span>
                    </div>
                    <button class="panel-action-btn" onclick="copyConsoleLogs()">Salin Log</button>
                </div>
                <div class="terminal-box" id="uiTerminalBox">Memuat aktivitas log...</div>
            </div>
        </div>

        <!-- FOOTER -->
        <footer class="page-footer">
            <span>IDX Sentinel Console &bull; Host: <code>202.10.47.34:7860</code></span>
            <span>Smooth Real-Time Polling &bull; Tanpa Refresh Halaman</span>
        </footer>
    </div>

    <script>
        function colorizeLog(line) {{
            let escaped = line.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
            let cls = "";
            if (line.includes("-> Terkirim:") || line.includes("lolos filter")) {{
                cls = "log-success";
            }} else if (line.includes("Insight AI") || line.includes("Gemini")) {{
                cls = "log-ai";
            }} else if (line.includes("Cloudflare") || line.includes("Status Halaman")) {{
                cls = "log-idx";
            }} else if (line.includes("Gagal") || line.includes("Error") || line.includes("⚠️")) {{
                cls = "log-danger";
            }} else if (line.includes("Notice") || line.includes("Timeout")) {{
                cls = "log-warn";
            }}
            return `<div class="log-row ${{cls}}">${{escaped}}</div>`;
        }}

        async function updateDashboard() {{
            try {{
                const res = await fetch("/api/dashboard-data");
                if (!res.ok) return;
                const data = await res.json();

                // Update Clock & Status
                if (data.time) document.getElementById("uiClock").innerText = data.time;
                document.getElementById("uiStateText").innerText = data.state || "Online";
                document.getElementById("uiWorkerState").innerText = data.state || "Standby";
                document.getElementById("uiTotalSent").innerText = data.total_sent + " Dokumen";
                document.getElementById("uiLastScan").innerText = data.last_scan || "--:--:--";
                if (data.model && document.getElementById("uiAiModel")) document.getElementById("uiAiModel").innerText = data.model;

                // Update Feed
                const feedEl = document.getElementById("uiFeedList");
                const items = data.last_items || [];
                document.getElementById("uiItemsCount").innerText = items.length + " Item";

                if (items.length === 0) {{
                    feedEl.innerHTML = `<div class="feed-empty">Menunggu pengumuman baru dari Bursa Efek Indonesia...</div>`;
                }} else {{
                    feedEl.innerHTML = items.slice().reverse().map(it => `
                        <div class="feed-item">
                            <span class="ticker-tag">${{it.emiten || "BEI"}}</span>
                            <div class="feed-info">
                                <span class="feed-title">${{it.judul}}</span>
                                <span class="feed-time">Terkirim: ${{it.time || "-"}} WIB</span>
                            </div>
                        </div>
                    `).join("");
                }}

                // Update Terminal Logs
                const term = document.getElementById("uiTerminalBox");
                const logs = data.logs || [];
                if (logs.length > 0) {{
                    term.innerHTML = logs.map(colorizeLog).join("");
                    term.scrollTop = term.scrollHeight;
                }}
            }} catch (err) {{
                console.warn("Polling error:", err);
            }}
        }}

        function copyConsoleLogs() {{
            const term = document.getElementById("uiTerminalBox");
            if (!term) return;
            navigator.clipboard.writeText(term.innerText).then(() => {{
                alert("Log konsol berhasil disalin ke clipboard!");
            }});
        }}

        // Auto poll dynamically every 3 seconds without full-page blinking!
        setInterval(updateDashboard, 3000);
        updateDashboard();
    </script>
</body>
</html>"""

if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
