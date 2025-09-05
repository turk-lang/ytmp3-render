import os
import re
import shutil
from flask import Flask, request, render_template_string, send_from_directory
from yt_dlp import YoutubeDL

# ==== Yapılandırma ====
COOKIE_SRC = "/etc/secrets/cookies.txt"   # Secret Files (read-only)
COOKIE_RT  = "/tmp/cookies.txt"           # Çalışma kopyası (yazılabilir)
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ==== Yardımcılar ====
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

            # --- Cookie: read-only → /tmp kopyası ---
            cookie_ok = False
            try:
                if os.path.exists(COOKIE_SRC):
                    with open(COOKIE_SRC, "rb") as src, open(COOKIE_RT, "wb") as dst:
                        dst.write(src.read())
                    os.chmod(COOKIE_RT, 0o600)
                    cookie_ok = os.path.getsize(COOKIE_RT) > 0
            except Exception as ce:
                print("COOKIE PREP ERROR:", ce)
            print("COOKIE_FOUND=", cookie_ok, "SRC=", COOKIE_SRC, "RT=", COOKIE_RT)

            # --- Ortak yt-dlp başlıkları ---
            common_headers = {
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": "en-US,en;q=0.9",
            }

            # ----- 1) PROBE: formatları listele (indirimsiz) -----
            probe_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": True,
                "http_headers": common_headers,
                "cachedir": False,
                "extract_flat": False,
            }
            if cookie_ok:
                probe_opts["cookiefile"] = COOKIE_RT
                probe_opts["extractor_args"] = {"youtube": {"player_client": ["web"]}}
            else:
                probe_opts["extractor_args"] = {"youtube": {"player_client": ["android", "tv"]}}

            chosen_format = None
            info = None
            try:
                with YoutubeDL(probe_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                formats = (info or {}).get("formats") or []

                # Ses adayları: audio-only ya da ses içerenler
                cands = []
                for f in formats:
                    vcodec = f.get("vcodec")
                    acodec = f.get("acodec")
                    if (vcodec == "none") or (acodec not in (None, "none")):
                        cands.append(f)

                # Puanlama: m4a > webm/opus > diğer; sonra bitrate
                def score(f):
                    ext = (f.get("ext") or "").lower()
                    pref = 2 if ext == "m4a" else (1 if ext in ("webm", "opus") else 0)
                    abr = f.get("abr") or f.get("tbr") or 0
                    return (pref, float(abr))
                if cands:
                    chosen_format = max(cands, key=score).get("format_id")
                print("CHOSEN_FORMAT_ID=", chosen_format)
            except Exception as pe:
                print("PROBE ERROR:", pe)

            # ----- 2) İNDİR: Seçilen format id ile -----
            ydl_opts = {
                "outtmpl": outtmpl,
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                # Yedek olarak genel seçici de dursun
                "format": chosen_format or "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
                "http_headers": common_headers,
                "cachedir": False,
            }
            if cookie_ok:
                ydl_opts["cookiefile"] = COOKIE_RT
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

            try:
                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)

                safe = re.sub(r'[\\/:*?"<>|]', "_", info.get("title", "audio"))
                if use_ff:
                    filename = f"{safe}.mp3"
                else:
                    ext = info.get("ext") or "m4a"
                    filename = f"{safe}.{ext}"
                msg = "✅ Dönüştürme tamam!"
            except Exception as e:
                import traceback
                print("YT-DLP ERROR:\n", traceback.format_exc())
                msg = f"❌ Hata: {type(e).__name__}: {e}"
                filename = None

    return render_template_string(HTML, msg=msg, filename=filename, last_url=last_url)
