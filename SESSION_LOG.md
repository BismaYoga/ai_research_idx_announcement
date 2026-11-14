# 📋 Log Sesi & Dokumentasi Pemeliharaan Bot IDX Announcement
**Tanggal Sesi:** 21 September 2026  
**Waktu Sesi:** 10:26 – 11:00 WIB  
**Repository / Folder:** [`/root/ai_research_idx_announcement`](file:///root/ai_research_idx_announcement)  
**Tujuan Dokumen:** Menjaga konteks percakapan, riwayat insiden, diagnosa error, langkah mitigasi yang telah diambil, serta panduan operasional agar sesi berikutnya dapat langsung melanjutkan tanpa kehilangan konteks.

---

## 1. Ringkasan Insiden Awal

* **Keluhan:** Pengecekan bot IDX Announcement apakah mengalami error.
* **Status Awal:** Bot mengalami kegagalan berulang (*stuck in an infinite error loop*) sejak **10:10:57 WIB** setiap interval 5 menit.
* **Gejala di Log (`bot.log`):**
  ```text
  [10:10:57] ⚠️ Gagal scan IDX (Page.goto: Page crashed
  Call log:
    - navigating to "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/", waiting until "domcontentloaded"
  ). Akan dicoba lagi...
  [10:15:57] Koneksi awal ke IDX lambat (Page.goto: Page crashed ...)
  [10:20:57] ⚠️ Gagal scan IDX (Page.goto: Page crashed ...)
  [10:25:57] ⚠️ Gagal scan IDX (Page.goto: Page crashed ...)
  [10:30:57] ⚠️ Gagal scan IDX (Page.goto: Page crashed ...)
  ```

---

## 2. Akar Masalah (*Root Cause Analysis*)

Dari penelusuran sistem dan kode program, ditemukan 3 faktor penyebab utama:

1. **Tab Browser Rusak Tidak Di-restart (*Zombie Page Loop*)**:
   - Di loop worker [`app.py`](file:///root/ai_research_idx_announcement/app.py), kondisi restart hanya memeriksa `if not browser.is_connected() or page.is_closed(): break`.
   - Saat proses renderer Chromium mati (`Page crashed`), nilai `page.is_closed()` dalam Playwright tetap `False`.
   - Akibatnya, bot terus memakai objek `page` yang sudah mati di setiap siklus berikutnya tanpa pernah membuat sesi browser baru.

2. **OOM Crash Renderer Akibat Pembatasan V8 JS yang Terlalu Ketat**:
   - Pada pukul 10:09 WIB, Cloudflare memicu challenge Turnstile (*"Just a moment..." / "Melakukan verifikasi keamanan"*).
   - Di konfigurasi launch browser terdapat argumen `--js-flags=--max-old-space-size=128` (dibatasi 128 MB).
   - Eksekusi script berat Cloudflare menyebabkan memori V8 meluap (*FatalProcessOutOfMemory*) sehingga renderer browser langsung mati (*crashed*).

3. **Swap Memory Server Penuh & Penumpukan Proses Yatim (*Orphan Xvfb*)**:
   - Server hanya memiliki RAM ~1 GB (961 MB).
   - File swap server hanya berukuran 256 MB dan telah terpakai 255.7 MB (**99.9% penuh**).
   - Terdapat banyak proses `Xvfb` yatim dari hari-hari sebelumnya (:99 hingga :107) yang menahan memori RAM.

4. **Kegagalan Deteksi Checkbox Turnstile**:
   - Selector `div.ctp-checkbox` / `span.ctp-checkbox-label` tidak dapat menemukan elemen checkbox karena Turnstile berada di dalam Shadow DOM / canvas tertutup.

---

## 3. Tindakan Perbaikan yang Telah Dilakukan

### A. Optimasi OS & Memori Server
1. **Pelebaran Swap File:**
   - Swap dinaikkan dari 256 MB menjadi **2.0 GB** menggunakan `/swapfile`.
   - Hasil: Memori swap saat ini memiliki sisa lega ~1.6 GB.
2. **Pembersihan Proses Yatim:**
   - Menghentikan proses `Xvfb` yatim lama (PID 7154, 7345, 7536, 7885, 8100, 8327, 8825, 11292, 61318).
   - Membersihkan lock file di `/tmp/xvfb-run.*` dan `/tmp/.X*-lock`.
   - Beban RAM tersedia kembali ke level aman (~240-250 MB free/available).

### B. Modifikasi Kode [`app.py`](file:///root/ai_research_idx_announcement/app.py)
1. **Penyesuaian Heap V8 (`launch_kwargs`):**
   - Mengubah `--js-flags=--max-old-space-size=128` menjadi `--js-flags=--max-old-space-size=384`. Memberikan ruang aman untuk Cloudflare tanpa membebani server 1GB.
2. **Auto-Click Koordinat Cloudflare Turnstile (`wait_for_cards`):**
   - Menambahkan deteksi frame Cloudflare dan melakukan klik langsung pada bounding box iframe:
     ```python
     fe = frame.frame_element()
     box = fe.bounding_box()
     if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
         page.mouse.click(box["x"] + 25, box["y"] + box["height"] / 2)
     ```
3. **Mekanisme Auto-Recovery dari Crash (`run_worker`):**
   - Menambahkan deteksi fatal browser error:
     ```python
     is_fatal = any(k in err_str.lower() for k in [
         "crashed", "crash", "target closed", "connection closed", "session closed", "browser has been closed"
     ])
     if is_fatal or not browser.is_connected() or page.is_closed():
         log_event("🔄 Browser tab/proses crash atau sesi terputus. Me-restart sesi browser...")
         # Tutup context & browser, lalu break agar loop me-relaunch Playwright
         ...
         break
     ```
   - Memastikan `browser.close()` selalu terpanggil dalam blok `finally`.

### C. Daemonisasi Ulang Proses Bot
- Menjalankan bot menggunakan perintah:
  ```bash
  setsid nohup xvfb-run -a python3 app.py >> /root/ai_research_idx_announcement/bot.log 2>&1 &
  ```

---

## 4. Hasil Verifikasi & Log Siklus Pengecekan

Setelah perbaikan diterapkan, bot telah melalui beberapa siklus scan 5-menitan tanpa error:

1. **Siklus 1 (10:45 WIB):**
   - Melakukan deteksi awal, lulus filter Cloudflare.
   - Hasil: `5 lolos filter, 0 pengumuman baru.`
2. **Siklus 2 (10:50 WIB):**
   - Status Halaman langsung: `'Keterbukaan Informasi'` (durasi scan hanya ~4 detik).
   - Hasil: `4 lolos filter, 0 pengumuman baru.`
3. **Siklus 3 (10:55 WIB):**
   - Status Halaman langsung: `'Keterbukaan Informasi'`.
   - Hasil: `1 lolos filter, 0 pengumuman baru.`
4. **Dashboard Web (Port 7860):**
   - Endpoint `/health` merespons `{"status": "ok", "total_sent": 0}`.
   - Halaman dashboard menampilkan `Standby (Cek berikutnya dalam 5 mnt)`.

---

## 5. Panduan Pemeliharaan & Quick Commands

Jika di masa mendatang perlu memeriksa atau mengelola bot ini:

* **Melihat Log Real-time:**
  ```bash
  tail -f -n 50 /root/ai_research_idx_announcement/bot.log
  ```
* **Cek Proses Aktif:**
  ```bash
  ps aux | grep -iE "app.py|xvfb"
  ```
* **Cek Kondisi RAM & Swap:**
  ```bash
  free -h
  ```
* **Cek Status Dashboard Lokal:**
  ```bash
  curl -s http://127.0.0.1:7860/health
  ```
* **Cara Restart Bersih (Jika Diperlukan):**
  ```bash
  pkill -9 -f "app.py"
  pkill -9 -f "Xvfb"
  cd /root/ai_research_idx_announcement
  setsid nohup xvfb-run -a python3 app.py >> /root/ai_research_idx_announcement/bot.log 2>&1 &
  ```

---

## 6. Update Sesi: Pembuatan & Redesign Web App "Nexus Console"
* **Waktu:** 11:02 – 11:16 WIB
* **URL Publik:** `http://202.10.47.34:8000`
* **Direktori:** `/root/simple-web`
* **Redesign Dilakukan:**
  - Mengganti gaya generic AI template (emoji berlebih, border neon warna-warni) dengan **Linear / Vercel minimalist design system** (deep zinc `#09090b`, clean typography *Plus Jakarta Sans* & *JetBrains Mono*, monochrome SVG line icons).
   - Mengimplementasikan **Mobile-First App Layout**:
     - Bilah navigasi bawah tetap (*fixed native bottom navigation bar*) di layar smartphone.
     - Kontrol sentuh nyaman (touch target > 44px) tanpa overflow horizontal.
     - Sinkronisasi otomatis tab (Overview, IDX Calc, Tasks, Scratchpad) antara tampilan mobile dan desktop.
     - Dukungan penuh Dark & Light theme dengan persistence `localStorage`.

---

## 6. [2026-09-21 11:30 WIB] Redesign UI IDX Announcement Dashboard (Port 7860) & Daemonisasi Systemd

### Latar Belakang & Masalah
- UI default sebelumnya tampak seperti template AI generik (border neon ungu/biru tebal, emoji berlebihan 📈📢📜, layout tabel/list kaku).
- Menggunakan tag `<meta http-equiv="refresh" content="5">` yang mengakibatkan seluruh halaman berkedip (*full-page flicker*) putih/hitam setiap 5 detik.
- Tidak responsif di perangkat seluler / smartphone.

### Solusi & Pembaruan
1. **Estetika Human-Engineered Modern (Linear / Raycast / Vercel style)**:
   - Palette warna deep zinc (`#09090b` base, `#121215` card surface, `#18181b` inner console).
   - Tipografi presisi: **Plus Jakarta Sans** untuk headline & metrik, **JetBrains Mono** untuk status badge, timestamp, dan stream log aktivitas.
   - Bento-card grid 4 metrik: Worker Status (dengan live pulsating dot indicator), Total Dokumen Terkirim, Waktu Terakhir Scan, dan AI Model Engine.
2. **Smooth AJAX Real-Time Polling (No Page Reload)**:
   - Menambahkan endpoint REST `GET /api/dashboard-data` pada FastAPI [`app.py`](file:///root/ai_research_idx_announcement/app.py).
   - Frontend melakukan polling via `fetch()` setiap 3 detik secara asinkron tanpa reload halaman, memperbarui counter, status, feed kartu, dan log secara seamless.
3. **Responsive Mobile-Ready**:
   - Desain responsif 1-kolom saat dibuka di viewport layar kecil / HP (< 880px).
   - Terminal aktivitas worker otomatis menyesuaikan ukuran tanpa memicu horizontal scrolling.
4. **Daemonisasi Layanan via Systemd**:
   - Dibuatkan service file `/etc/systemd/system/idx-announcement.service` dengan auto-restart (`Restart=always`, `RestartSec=5`).
   - Bot berjalan mandiri di background dan aman dari disconnect terminal/session.
   - Status service: `systemctl status idx-announcement` aktif dan berjalan normal.

---

## 7. [2026-09-21 20:20 WIB] Perbaikan Model AI Gemini & Penambahan Comprehensive Error Handling

### Latar Belakang & Gejala Error
* **Waktu Kejadian:** 18:40 – 20:18 WIB
* **Gejala di Log (`bot.log`):**
  - Model utama `gemini-3.6-flash` mengalami lonjakan beban di sisi Google API (*503 UNAVAILABLE: This model is currently experiencing high demand*).
  - Bot mencoba beralih ke model cadangan, namun gagal total dengan error:
    ```text
    [20:17:47] Model gemini-3.6-pro notice: 404 NOT_FOUND
    [20:17:48] Model gemini-3-flash notice: 404 NOT_FOUND
    [20:17:48] Gemini AI error: 404 NOT_FOUND
    ```
  - Akibatnya, notifikasi pengumuman emiten (seperti `[DYAN]` dan `[BSSR]`) terkirim ke Telegram tanpa insight/ringkasan AI.

### Akar Masalah (*Root Cause*)
1. **Nama Fallback Model Tidak Valid (404 NOT_FOUND)**:
   - Model `gemini-3.6-pro` dan `gemini-3-flash` yang di-hardcode tidak tersedia di Google Gemini API.
2. **Warning AFC (Automatic Function Calling) Membanjiri Log**:
   - SDK `google-genai` versi 2.24.0 mengaktifkan AFC secara default sehingga memunculkan peringatan logger berulang.
3. **Risiko Penumpukan File di Gemini Files API**:
   - Dokumen PDF yang diunggah ke Google Gemini Files API tidak dihapus setelah analisis selesai.
4. **Risiko Kegagalan Kirim Telegram pada Format HTML Tak Valid**:
   - Jika ringkasan AI menghasilkan unclosed HTML tag, Telegram API menolak seluruh pesan (*400 Bad Request: can't parse entities*).

### Solusi & Implementasi
1. **Model Upgrade & Multi-Tier Fallback Chain**:
   - Model utama di `.env` diubah ke `gemini-3.7-flash` yang terbukti responsif dan stabil.
   - Menyusun rantai fallback otomatis yang terverifikasi aktif pada API:
     `["gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.6-flash"]`.
   - Fallback dapat dikonfigurasi via environment variable `GEMINI_FALLBACK_MODELS`.
2. **Error Handling & Retry Cerdas**:
   - **503 UNAVAILABLE (Server Padat):** Retry cepat 1x (2 detik); jika masih padat, langsung beralih ke model cadangan berikutnya tanpa menunda proses bot.
   - **429 RESOURCE_EXHAUSTED (Rate Limit):** Jika retry delay <= 12s, tunggu sebentar; jika lebih panjang, langsung fallback ke model lain.
   - **404 NOT_FOUND / Deprecated:** Langsung melompat ke model cadangan.
   - **401 UNAUTHENTICATED:** Berhenti mencoba dan mencatat peringatan otentikasi API Key.
3. **Timeout & Suppression AFC**:
   - Inisialisasi client dengan `HttpOptions(timeout=45000)` dan `GenerateContentConfig(automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))`.
   - Meredam logger AFC di level `ERROR`.
4. **Lifecycle Management File Remote**:
   - Blok `finally` di `analisis_dokumen` kini secara eksplisit memanggil `ai_client.files.delete(name=uploaded.name)`.
5. **Fail-Safe Pengiriman Telegram**:
   - Di `kirim_telegram`, jika terjadi error *can't parse entities* akibat format HTML, bot otomatis mengirim ulang sebagai plain text (tag HTML dibersihkan), menjamin pengumuman tidak pernah hilang.
6. **Monitoring Model di Web Dashboard**:
   - Endpoint `/api/dashboard-data` dan UI dashboard (port 7860) menampilkan model aktif secara dinamis.
