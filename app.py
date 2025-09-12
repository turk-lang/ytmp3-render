import os
import re
import shutil
import time
from flask import Flask, request, render_template_string, send_file
from yt_dlp import YoutubeDL

app = Flask(__name__)

DOWNLOAD_DIR = "/tmp"

HTML_FORM = """
<!DOCTYPE html>
<html lang="tr">
<head><meta charset="UTF-8"><title>YT MP3 Converter</title></head>
<body>
  <h2>YouTube → MP3</h2>
  <form method="POST">
    <input type="text" name="url" placeholder="YouTube linki" size="50" required>
    <button type="submit">İndir</button>
  </form>
  {% if msg %}<p>{{ msg }}</p>{% endif %}
  {% if filename %}<a href="/download/{{ filename }}">Dosyayı indir</a>{% endif %}
  <p style="color:#777;font-size:12px">Not: FFmpeg varsa MP3'e dönüştürülür; yoksa orijinal ses uzantısı kalır.</p>
</body></html>
"""

# --- NEW: Secret File -> /tmp/cookies.txt
def ensure_cookiefile() -> str | None:
    """
    Render Secret Files (veya ENV ile verilen) cookies.txt'yi /tmp/cookies.txt'ye kopyalar.
    Başarılıysa yol döner, yoksa None.
    """
    candidates = [
        os.environ.get("YTDLP_COOKIES"),
        "/etc/secrets/cookies.txt",
        "/etc/secrets/COOKIES.txt",
        "/etc/secrets/youtube-cookies.txt",
        "/app/cookies.txt",  # local fallback (geliştirmede)
    ]
    for src in candidates:
        if src and os.path.exists(src) and os.path.getsize(src) > 0:
            try:
                dst = "/tmp/cookies.txt"
                # Kopyala sadece değişmişse
                if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
                    shutil.copyfile(src, dst)
                age = int(time.time() - os.path.getmtime(dst))
                try:
                    lines = sum(1 for _ in open(dst, "r", encoding="utf-8", errors="ignore"))
                except Exception:
                    lines = -1
                print(f"[cookies] using: {dst} age={age}s lines={lines}")
                return dst
            except Exception as e:
                print(f"[cookies] copy failed {src} -> /tmp/cookies.txt : {e}")
    print("[cookies] not found")
    return None

def get_base_opts():
    # Her çağrıda emin olmak için cookie’yi hazırla
    cookie_path = ensure_cookiefile()

    opts = {
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title).90s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "cachedir": False,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "nocheckcertificate": True,
        "source_address": "0.0.0.0",
        "http_headers": {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "en-US,en;q=0.9"
        },
        "extractor_args": {
            "youtube": {
                # cookie varsa web'i öne al; yoksa tv/android ile başla
                "player_client": ["web", "tv", "android"] if cookie_path else ["tv", "android", "web"],
                "skip": ["configs"],
            }
        },
        "geo_bypass_country": "US",
    }
    if cookie_path:
        opts["cookiefile"] = cookie_path
    return opts

def extract_meta(url):
    opts = get_base_opts()
    opts["skip_download"] = True
    with YoutubeDL(opts) as y:
        return y.extract_info(url, download=False)

def pick_format(meta):
    """Öncelik: m4a > opus/webm > başka audio-only > best"""
    fmts = (meta or {}).get("formats") or []
    audio_only = [f for f in fmts if f.get("vcodec") in (None, "none") and (f.get("acodec") not in (None, "none"))]

    m4a = [f for f in audio_only if (f.get("ext") or "").lower() == "m4a"]
    if m4a:
        return m4a[0]["format_id"]

    opus = [f for f in audio_only if (f.get("ext") or "").lower() in ("webm", "opus")]
    if opus:
        return opus[0]["format_id"]

    if audio_only:
        audio_only.sort(key=lambda f: float(f.get("abr") or f.get("tbr") or 0), reverse=True)
        return audio_only[0]["format_id"]

    return "best"

def run_download(url, fmt):
    opts = get_base_opts()
    use_ff = shutil.which("ffmpeg") is not None
    if fmt:
        opts["format"] = fmt
    if use_ff:
        opts.update({
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192"
            }],
            "postprocessor_args": ["-ar", "44100"],
            "prefer_ffmpeg": True,
        })
    with YoutubeDL(opts) as y:
        return y.extract_info(url, download=True)

@app.route("/", methods=["GET", "POST"])
def index():
    msg = None
    filename = None
    if request.method == "POST":
        url = request.form.get("url", "").strip()
        if not url:
            msg = "❌ URL gerekli"
        else:
            try:
                meta = extract_meta(url)
                fmt_id = pick_format(meta)
                print(f"[format] seçilen fmt_id={fmt_id}")
                info = run_download(url, fmt_id)

                safe = re.sub(r'[\\/:*?"<>|]', "_", info.get("title", "audio"))
                ext = "mp3" if shutil.which("ffmpeg") else (info.get("ext") or "m4a")
                filename = f"{safe}.{ext}"
                filepath = os.path.join(DOWNLOAD_DIR, filename)

                if not os.path.exists(filepath):
                    msg = "⚠️ Dosya bulunamadı"
                else:
                    msg = "✅ Dönüştürme tamam!"
            except Exception as e:
                import traceback
                msg = f"❌ İndirme Hatası: {e}"
                print("Hata:", traceback.format_exc())

    return render_template_string(HTML_FORM, msg=msg, filename=filename)

@app.route("/download/<path:filename>")
def download(filename):
    return send_file(os.path.join(DOWNLOAD_DIR, filename), as_attachment=True)

@app.route("/healthz")
def health():
    return "ok"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
