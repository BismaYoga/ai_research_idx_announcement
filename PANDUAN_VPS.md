# 📖 Panduan Penggunaan Bot IDX di VPS (SSH, Aktifkan & Nonaktifkan)

Panduan lengkap untuk mengelola bot pengumuman IDX & AI Insight di server VPS Ubuntu/Debian.

---

## 1. Cara Masuk (SSH) ke Server VPS

Buka terminal di komputer lokal Anda (**Command Prompt**, **PowerShell**, atau **Terminal Mac/Linux**), lalu jalankan:

```bash
ssh root@<IP_VPS_ANDA>
```
> *Ganti `<IP_VPS_ANDA>` dengan alamat IP publik VPS Anda, lalu masukkan password saat diminta.*

Setelah berhasil login, masuk ke folder project bot:
```bash
cd ~/ai_research_idx_announcement
```

---

## 2. Cara Mengaktifkan Bot (Jalan Nonstop 24/7)

Agar bot tetap berjalan di latar belakang (*background*) meskipun jendela terminal/laptop Anda ditutup, gunakan perintah:

```bash
nohup xvfb-run -a python3 app.py > bot.log 2>&1 &
```

### Penjelasan Perintah:
* **`nohup`**: Menjaga proses tetap hidup meskipun sesi SSH terputus (*no hang up*).
* **`xvfb-run -a`**: Menjalankan virtual display agar browser Playwright Chromium terdeteksi sebagai browser desktop asli (bukan headless) sehingga aman dari Cloudflare.
* **`> bot.log 2>&1`**: Mengarahkan semua output catatan aktivitas ke berkas `bot.log`.
* **`&`**: Melempar proses langsung ke latar belakang.

---

## 3. Cara Memantau Aktivitas Bot (Monitoring)

### A. Melihat Catatan Log Secara Langsung (Live)
Untuk memantau apa yang sedang dikerjakan bot secara *real-time*:
```bash
tail -f bot.log
```
> **Catatan Penting:** Untuk keluar dari tampilan pantauan log dan kembali ke command line, tekan tombol **`Ctrl + C`**. Bot **akan tetap terus berjalan** di background.

### B. Melihat Cuplikan Log Terakhir
Jika hanya ingin melihat sekilas status terakhir tanpa memantau terus:
```bash
tail -n 30 bot.log
```

### C. Mengecek Apakah Bot Sedang Berjalan
```bash
ps aux | grep app.py
```
* Jika muncul baris berisi `python3 app.py` dan `xvfb-run`, bot **aktif**.
* Jika tidak muncul, bot **mati/berhenti**.

### D. Memantau via Web Dashboard
Buka browser di HP atau Laptop Anda, lalu akses:
```text
http://<IP_VPS_ANDA>:7860
```
*(Menampilkan status bot, scan terakhir, dan ringkasan pengumuman yang berhasil dikirim).*

---

## 4. Cara Menonaktifkan (Mematikan) Bot

Jika Anda ingin menghentikan bot:

### Cara Cepat (Rekomendasi):
```bash
pkill -f app.py
```

### Verifikasi:
Ketik perintah berikut untuk memastikan bot sudah berhenti:
```bash
ps aux | grep app.py
```
*(Proses `app.py` sudah tidak ada lagi)*.

---

## 5. Cara Merestart Bot (Misal Setelah Update Kode)

Jika ada perubahan kode baru dari GitHub atau Anda mengubah file `.env`:

```bash
# 1. Hentikan bot yang sedang berjalan
pkill -f app.py

# 2. Ambil pembaruan kode terbaru (opsional jika ada update)
git pull origin main

# 3. Jalankan kembali bot di background
nohup xvfb-run -a python3 app.py > bot.log 2>&1 &

# 4. Cek log untuk memastikan sudah jalan
tail -f bot.log
```

---

## 6. Tips Tambahan: Cegah Crash di VPS Spek Kecil (Swap 2GB)

Jika VPS Anda memiliki RAM 1GB atau 512MB, pastikan SWAP aktif agar proses Chromium tidak dimatikan oleh sistem Linux:

```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab
```
*Cek status memori dengan perintah: `free -h`*
