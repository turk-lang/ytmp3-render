# Python 3.11 + ffmpeg
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# sistem bağımlılıkları (ffmpeg dahil)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates tzdata curl \
    && rm -rf /var/lib/apt/lists/*

# çalışma dizini
WORKDIR /app

# minimal bağımlılıklar
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# kod
COPY app.py /app/app.py
RUN mkdir -p /app/downloads

# health: yt-dlp sürüm yaz (opsiyonel)
RUN python -c "import yt_dlp,sys;print('yt-dlp:',yt_dlp.version.__version__);sys.stdout.flush()"

# Render PORT'u env olarak verir
EXPOSE 10000
CMD ["bash","-lc","gunicorn app:app -b 0.0.0.0:${PORT:-10000} --timeout 600 --workers 1 --threads 2 --keep-alive 120"]
