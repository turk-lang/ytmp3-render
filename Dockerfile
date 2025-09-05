# Küçük taban görüntü
FROM python:3.11-slim

# FFmpeg kur
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# Çalışma dizini
WORKDIR /app

# Bağımlılıklar
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama
COPY . .

# İndirmeler için klasör
RUN mkdir -p /app/downloads

# Render PORT değişkenini sağlar; yoksa 10000 kullan
CMD ["gunicorn", "app:app", "-b", "0.0.0.0:${PORT:-10000}", "--timeout", "600", "--workers", "1", "--threads", "2", "--keep-alive", "120"]
