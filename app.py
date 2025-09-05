import os
import re
import shutil
from datetime import datetime
from flask import Flask, request, render_template_string, send_from_directory, url_for
from yt_dlp import YoutubeDL
COOKIE_PATH = "/etc/secrets/cookies.txt"



BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = Flask(__name__)

HTML = """
<!doctype html>
<meta charset="utf-8">
<title>🎵 YouTube MP3 İndirici</title>
<h1>🎵 YouTube MP3 İndirici</h1>

<form method="post">
  <input name="url" placeholder="YouTube linkini yapıştır" style="width:420px" value="{{ last_url or '' }}" required>
  <button type="submit">MP3'e Dönüştür</button>
</form>

{% if msg %}
  <p style="margin-top:1rem;">{{ msg|safe }}</p>
{% endif %}

{% if filename %}
  <p>
    ✅ Hazır: <a href="{{ url_for('download_file', filename=filename) }}">{{ filename }}</a>
    <br><small>(Dosyayı indirmek için tıkla.)</small>
  </p>
{% endif %}

<hr>
<p style="margin-top:1rem;font-size:.9em;opacity:.7">
  Not: FFmpeg bulunduğunda MP3'e dönüştürülür; aksi halde orijinal ses formatı (m4a/webm) indirilir.
</p>
"""

def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None

def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\s\-\.\(\)]", "", name, flags=re.UNICODE).strip()
    name = re.sub(r"\s+", " ", name)
    return name or datetime.now().strftime("%Y%m%d_%H%M%S")
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

            # --- Cookie ve yt-dlp ayarları (girinti ÖNEMLİ) ---
            cookie_ok = os.path.exists(COOKIE_PATH) and os.path.getsize(COOKIE_PATH) > 0

            ydl_opts = {
                "outtmpl": outtmpl,
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "format": "bestaudio/best",
                "http_headers": {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"},
                "cachedir": False,
            }

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
            # ---------------------------------------------------

            try:
                # İNDİRME
                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    # ffmpeg yoksa indirilen orijinal uzantıyı bul
                    if not use_ff:
                        ext = info.get("ext") or "m4a"
                        safe = re.sub(r"[\\/:*?\"<>|]", "_", info.get("title", "audio"))
                        filename = f"{safe}.{ext}"
                    else:
                        safe = re.sub(r"[\\/:*?\"<>|]", "_", info.get("title", "audio"))
                        filename = f"{safe}.mp3"
                msg = "✅ Tamam!"
            except Exception as e:
                import traceback
                print("YT-DLP ERROR:", traceback.format_exc())
                msg = f"❌ Hata: {type(e).__name__}: {e}"
                filename = None

    return render_template_string(HTML, msg=msg, filename=filename, last_url=last_url)


@app.route("/downloads/<path:filename>")
def download_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

# Render port'u dinlemek istersen yerelde de çalışır:
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
