@echo off
title IDX Telegram Notifier ^& AI Insight Bot
color 0B
cd /d "%~dp0"

:: Mode Lokal: Tanpa Proxy (Direct Connection ke IDX)
set ENABLE_PROXY=false

echo ===================================================================
echo             IDX TELEGRAM NOTIFIER ^& AI BOT (MODE LOKAL)
echo ===================================================================
echo.
echo Mode: Direct Connection (Tanpa Proxy, menggunakan koneksi lokal)
echo Sedang memulai bot...
echo Dashboard live dapat dibuka di browser: http://localhost:7860
echo.
echo Tekan CTRL+C jika ingin menghentikan bot.
echo ===================================================================
echo.

python app.py

pause
