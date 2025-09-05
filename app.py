import os
import re
import shutil
from datetime import datetime
from flask import Flask, request, render_template_string, send_from_directory
from yt_dlp import YoutubeDL

# === Yapılandırma ===
COOKIE_PATH = "/etc/secrets/cookies.txt"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# === Yardımcılar ===
def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None

YOUTUBE_RE = re.compile(r"(youtu\.be/|youtube\.com/)")
def is_youtube_url(u: str) -> bool:
    return bool(u and YOUTUBE_RE.search(u))

HTML = """
<!doctype html>
<title>🎵 YouTube MP3 İndirici</title>
<h1>🎵 YouTube MP3 İndirici</h1>
<form method="post">
  <input type="text" name="url" value="{{ last_url or '' }}" style="width:70%%">
  <button type="submit">MP3'e Dönüştür</button>
</form>
{% if msg %}<p>{{ msg|safe }}</p>{% endif %}
{% if filename %}
  <p>✅ <a href="/downloads/{{ filename }}">İndir: {{ filename }}</a></p>
{% endif %}
<p style="opacity:.6;font-size:12px;">Not: FFmpeg varsa MP3'e dönüştürülür; yoksa orijinal ses indirilir.</p>
"""

app = Flask(__name__)

@app.route("/downloads/<path:name>")
def dl(name):
    return send_from_directory(DOWNLOAD_DIR, name, as_attachment=True)

@app.route("/", methods=["GET", "POST"])
def index():
    msg = ""
    filename = None
    last_url = ""

    if request.method == "POST":
        url = (request.form.get("url") or "").strip()
        last_url = url

        if not is_youtube_url(url):
            msg = "❌ Lütfen geçerli bir YouTube video bağlantısı girin."
        else:
            use_ff = has_ffmpeg()
            outtmpl = os.path.join(DOWNLOAD_DIR, "%(title).90s.%(ext)s")

            # --- Cookie (yalnızca OKUMA; read-only mount) ---
            cookie_ok = False
            try:
                if os.path.exists(COOKIE_PATH):
                    with open(COOKIE_PATH, "rb") as f:
                        cookie_ok = len(f.read().strip()) > 0
            except Exception as ce:
                print("COOKIE CHECK ERROR:", ce)
            print("COOKIE_FOUND=", cookie_ok, "PATH=", COOKIE_PATH)

            # --- yt-dlp ayarları ---
            ydl_opts = {
    "outtmpl": outtmpl,
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    # Eğer opus/m4a varsa onları, yoksa bestaudio
    "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
    "http_headers": {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
    },
    "cachedir": False,
}


            # Cookie varsa web client; yoksa android/tv fallback
            if cookie_ok:
                ydl_opts["cookiefile"] = COOKIE_PATH
                ydl_opts["extractor_args"] = {"youtube": {"player_client": ["web"]}}
            else:
                ydl_opts["extractor_args"] = {"youtube": {"player_client": ["android", "tv"]}}

            if use_ff:
                ydl_opts.update({
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                    "postprocessor_args": ["-ar", "44100"],
                    "prefer_ffmpeg": True,
                })

            # --- İndirme ---
            try:
                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)

                safe_title = re.sub(r'[\\/:*?"<>|]', "_", info.get("title", "audio"))
                if use_ff:
                    filename = f"{safe_title}.mp3"
                else:
                    ext = info.get("ext") or "m4a"
                    filename = f"{safe_title}.{ext}"
                msg = "✅ Dönüştürme tamam!"
            except Exception as e:
                import traceback
                print("YT-DLP ERROR:\n", traceback.format_exc())
                msg = f"❌ Hata: {type(e).__name__}: {e}"
                filename = None

    return render_template_string(HTML, msg=msg, filename=filename, last_url=last_url)

# Gunicorn entrypoint: app
