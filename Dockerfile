FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Install Xvfb untuk virtual screen display di RAM
RUN apt-get update && apt-get install -y xvfb && rm -rf /var/lib/apt/lists/*

# Buat user non-root (Standar wajib Hugging Face Spaces UID 1000)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /home/user/app

# Install dependensi Python
COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy seluruh source code
COPY --chown=user:user . .

# Port default Hugging Face Spaces
EXPOSE 7860

# Jalankan dengan xvfb-run (virtual screen) agar Playwright berjalan normal di server
CMD ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1280x800x24", "python", "app.py"]
