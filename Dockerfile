FROM python:3.11-slim

# Hızlı ve temiz Python çıktısı
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# MP3 dönüşümü için ffmpeg şart
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Uygulama dizini
WORKDIR /app

# Bağımlılıklar
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama
COPY app.py .

# Kalıcı indirmeler için Render Disk'i /var/data'ya mount et
ENV DOWNLOAD_DIR=/var/data

# Render, dinleyecek portu $PORT ile verir
CMD ["bash","-lc","gunicorn -b 0.0.0.0:$PORT app:app"]
