FROM python:3.11-slim

# ffmpeg lazım olduğu için ekliyoruz
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg bash curl \
 && rm -rf /var/lib/apt/lists/*

# Çalışma dizini
WORKDIR /app

# Python bağımlılıklarını yükle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama dosyalarını kopyala
COPY . .

# İndirilen dosyalar için klasör
RUN mkdir -p /app/downloads

# Render’ın verdiği $PORT değişkeni shell form ile genişletilsin
# Ayrıca loglarda kontrol için PORT değerini ekrana yazdırıyoruz
CMD ["bash","-lc","echo Running on PORT=$PORT && exec gunicorn app:app -b 0.0.0.0:${PORT:-10000} --timeout 600 --workers 1 --threads 2 --keep-alive 120"]
