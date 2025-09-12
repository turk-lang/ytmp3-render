import os
import re
import time
import shutil
from flask import Flask, request, render_template_string, send_from_directory
from yt_dlp import YoutubeDL

app = Flask(__name__)

# ---- Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ---- Simple UI
HTML = """
<!doctype html>
<title>YouTube MP3 İndirici</title>
<h3>🎵 YouTube MP3 İndirici</h3>
<form method="get">
  <input style="width:520px" name="u" placeholder="https://www.youtube.com/watch?v=..." value="{{u or ''}}">
  <button>MP3'e Dönüştür</button>
</form>
{% if err %}<p style="color:#b00">❌ {{err}}</p>{% endif %}
{% if fn %}<p>✅ Hazır: <a href="/d/{{ fn }}">{{ fn }}</a></p>{% endif %}
<p style="font-size:12px;color:#777">Not: FFmpeg varsa MP3'e dönüştürülür; yoksa orijinal ses indirilir.</p>
"""

# ---- Cookies helper
def pick_cookiefile() -> str | None:
    """
    Render Secret Files için tipik yolları dener; bulursa /tmp/cookies.txt'e kopyalar.
    Ortam değişkeni YTDLP_COOKIES verilmişse onu önceliklendirir.
    """
    candidates = [
        os.environ.get("YTDLP_COOKIES"),
        "/etc/secrets/cookies.txt",
        "/etc/secrets/COOKIES.txt",
        "/etc/secrets/youtube-cookies.txt",
    ]
    for c in candidates:
        if c and os.path.exists(c) and os.path.getsize(c) > 0:
            try:
                dst = "/tmp/cookies.txt"
                shutil.copyfile(c, dst)
                age = int(time.time() - os.path.getmtime(dst))
                try:
                    lines = sum(1 for _ in open(dst, "r", encoding="utf-8", errors="ignore"))
                except Exception:
                    lines = -1
                print(f"[cookies] using: {dst} age={age}s lines={lines}")
                return dst
            except Exception as e:
                print(f"[cookies] copy failed {c} -> /tmp/cookies.txt : {e}")
    print("[cookies] not found")
    return None

# ---- yt-dlp options
def make_ydl(cookiefile: str | None, for_meta: bool = False) -> YoutubeDL:
    use_ff = shutil.which("ffmpeg") is not None
    postprocessors = []
    if use_ff and not for_meta:
        postprocessors = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    # YouTube anti-bot'a karşı client fallback ve sağlam header’lar
    extractor_args = {
        "youtube": {
            # cookie varsa web'i öne al; yoksa tv/android ile başla
            "player_client": ["web", "tv", "android"] if cookiefile else ["tv", "android", "web"],
            "skip": ["configs"],  # bazı bölgelerde gereksiz istekleri azaltır
        }
    }

    ydl_opts: dict = {
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title).80s.%(ext)s"),
        "quiet": True,
        "noprogress": False,
        "ignoreerrors": False,
        "retries": 3,
        "fragment_retries": 3,
        "nocheckcertificate": True,
        "nopart": True,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
        "extractor_args": extractor_args,
        "geo_bypass_country": "US",
        "postprocessors": postprocessors,
        "noplaylist": True,
        "no_warnings": True,
        "cachedir": False,
    }

    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile

    return YoutubeDL(ydl_opts)

# ---- Helpers
def sanitize_name(title: str, ext: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]', "_", title or "audio")
    return f"{safe}.{ext}"

# ---- Routes
@app.route("/")
def index():
    url = (request.args.get("u") or "").strip()
    if not url:
        return render_template_string(HTML, u=url)

    cookiefile = pick_cookiefile()

    def run_dl(fmt: str | None):
        y = make_ydl(cookiefile, for_meta=False)
        if fmt:
            y.params["format"] = fmt
        print(f"[yt-dlp] download fmt={fmt or 'auto'} cookie={'yes' if cookiefile else 'no'}")
        return y.extract_info(url, download=True)

    try:
        # 1) Önce bestaudio – cookie varsa genelde geçer
        info = run_dl("bestaudio/best")
    except Exception as e1:
        print("FIRST TRY FAILED:", e1)
        try:
            # 2) Meta çek, en iyi ses formatını seç, tekrar dene
            y2 = make_ydl(cookiefile, for_meta=True)
            meta = y2.extract_info(url, download=False)
            formats = (meta or {}).get("formats") or []
            # abr/tbr en yüksek olan ses akışını seç
            audio_formats = [f for f in formats if (f.get("acodec") and f.get("acodec") != "none")]
            audio_formats.sort(key=lambda f: float(f.get("abr") or f.get("tbr") or 0), reverse=True)
            chosen = audio_formats[0] if audio_formats else None
            fmt_id = (chosen.get("format_id") if chosen else None) or "bestaudio/best"
            print(f"[meta] picked format_id={fmt_id}")
            info = run_dl(fmt_id)
        except Exception as e2:
            print("SECOND TRY ERROR:", e2)
            return render_template_string(HTML, u=url, err=f"DownloadError: {e1} / {e2}")

    # Çıkan dosya ismi
    reqs = info.get("requested_downloads") or []
    fp = (reqs[0].get("filepath") or reqs[0].get("filename")) if reqs else None
    if not fp:
        fp = info.get("filepath") or info.get("filename")
    if not fp or not os.path.exists(fp):
        return render_template_string(HTML, u=url, err="İndirme başarılı ama dosya bulunamadı.")

    name = os.path.basename(fp)
    return render_template_string(HTML, u=url, fn=name)

@app.route("/d/<path:name>")
def d(name):
    return send_from_directory(DOWNLOAD_DIR, name, as_attachment=True)

@app.route("/healthz")
def health():
    return "ok"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
