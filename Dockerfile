FROM python:3.11-slim

# Daha temiz loglar
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# MP3 dönüşümü için ffmpeg
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Bağımlılıklar
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Uygulama
COPY app.py .

# İndirilen dosyalar için kalıcı disk
ENV DOWNLOAD_DIR=/var/data

# Dayanıklı Gunicorn ayarları (Render $PORT’u otomatik verir)
CMD ["bash","-lc","gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 300 --graceful-timeout 30 --keep-alive 75 --log-level info"]
