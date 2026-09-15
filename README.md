---
title: Idx Announcement
emoji: 📈
colorFrom: purple
colorTo: red
sdk: gradio
sdk_version: 6.27.0
python_version: '3.12'
app_file: app.py
pinned: false
---

# 📈 IDX Keterbukaan Informasi & AI Notifier (Gradio SDK - 100% Free Tier)

Bot otomatis pemantau keterbukaan informasi Bursa Efek Indonesia (IDX) dengan analisis AI Gemini dan notifikasi ke grup/channel Telegram. 

Dilengkapi dengan **sistem rotasi 30+ proxy server Indonesia** untuk menghindari blokir Cloudflare, serta dashboard interaktif **Gradio** yang berjalan di tier gratis Hugging Face Spaces (tanpa perlu kartu kredit/bayar).

---

## 🚀 Cara Deploy ke Hugging Face Spaces (Gratis)

1. Buka [Hugging Face](https://huggingface.co/) dan login ke akun Anda.
2. Klik **New Space** (atau kunjungi [huggingface.co/new-space](https://huggingface.co/new-space)).
3. Atur konfigurasi Space:
   * **Space name:** `idx-telegram-bot` (bebas)
   * **License:** `mit` / `apache-2.0`
   * **Space SDK:** Pilih **Gradio** *(100% Gratis - CPU basic 2 vCPU 16GB RAM)*
   * **Visibility:** **Public** atau **Private**
4. Klik **Create Space**.
5. Upload berkas berikut ke repositori Space tersebut:
   * `app.py`
   * `requirements.txt`
   * `packages.txt`
   * `seen_ids.json`
   * `README.md`
6. Tunggu proses **Building** sampai statusnya berubah menjadi **Running**!

---

## 🔒 Mengatur Variabel Rahasia (Secrets)

1. Di halaman Space Anda, buka tab **Settings**.
2. Gulir ke bawah ke bagian **Variables and secrets** > Klik tombol **New secret**.
3. Masukkan variabel rahasia berikut:
   * `TELEGRAM_TOKEN` : Token Bot Telegram Anda (misal: `8274193816:...`)
   * `TELEGRAM_CHAT_ID` : ID Chat / Channel Telegram tujuan (misal: `-5411895904`)
   * `API_AI` : API Key Google Gemini Anda
   * *(Opsional)* `PROXY_URL` : Custom proxy Indonesia khusus jika Anda punya proxy privat
   * *(Opsional)* `PROXY_LIST` : Daftar proxy tambahan dipisah koma
   * *(Opsional)* `ENABLE_PROXY` : `true` (default) atau `false`
   * *(Opsional)* `POLL_INTERVAL` : Interval pengecekan IDX dalam menit (default: `5`)
   * *(Opsional)* `GEMINI_MODEL` : Model Gemini (default: `gemini-2.5-flash`)

---

## 🌐 Dashboard Web Gradio

Setelah status Space berubah menjadi **Running**, halaman Space Anda akan menampilkan Dashboard Interaktif:
- **Status Bot:** Pemantauan real-time status scraper.
- **Proxy Aktif:** Menampilkan alamat IP proxy Indonesia yang sedang aktif digunakan.
- **Tabel Pengumuman:** Daftar pengumuman emiten yang baru saja dikirim ke Telegram.
- **Log Real-time:** Log deteksi Cloudflare dan rotasi switch-case proxy secara otomatis.
- **Tombol Aksi:** Tombol **Refresh Status** dan **Ganti Proxy Berikutnya** jika ingin melakukan switch proxy secara manual.
