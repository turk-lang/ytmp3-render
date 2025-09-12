FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["bash","-lc","gunicorn app:app -b 0.0.0.0:${PORT:-10000} --timeout 600 --workers 1 --threads 2 --keep-alive 120"]
