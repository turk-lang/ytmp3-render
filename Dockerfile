FROM python:3.11-slim

# ffmpeg for mp3 conversion
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Persistent downloads (attach a Render Disk to /var/data)
ENV DOWNLOAD_DIR=/var/data

# Start
CMD ["bash","-lc","gunicorn -b 0.0.0.0:$PORT app:app"]
