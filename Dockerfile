# Temel imaj
FROM python:3.11-slim

# Çalışma dizini
WORKDIR /app

# Gereksiz cache olmadan paketler
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama dosyaları
COPY . .

# Render PORT ortam değişkenini kullan
CMD ["bash", "-c", "gunicorn app:app -b 0.0.0.0:${PORT:-5000} --timeout 600 --workers 1 --threads 2 --keep-alive 120"]
