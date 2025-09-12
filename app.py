import os, shutil, time
from flask import Flask, request, render_template_string, send_from_directory, url_for
from yt_dlp import YoutubeDL

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def pick_cookiefile() -> str | None:
    """
    Render'da Secret File olarak eklediğin cookies.txt genellikle /etc/secrets/cookies.txt olur.
    Varsa /tmp/cookies.txt'e kopyalayıp onu kullanıyoruz (okuma-yazma güvenli).
    Yoksa None döner.
    """
    candidates = [
        os.environ.get("YTDLP_COOKIES"),           # istersen env ile de geçebilirsin
        "/etc/secrets/cookies.txt",
        "/etc/secrets/COOKIES.txt",
        "/etc/secrets/youtube-cookies.txt",
    ]
    for c in candidates:
        if c and os.path.exists(c) and os.path.getsize(c) > 0:
            try:
                dst = "/tmp/cookies.txt"
                shutil.copyfile(c, dst)
                # Dosya yaşı
                age_sec = time.time() - os.path.getmtime(dst)
                try:
                    lines = sum(1 for _ in open(dst, "r", encoding="utf-8", errors="ignore"))
                except Exception:
                    lines = -1
                print(f"[cookies] using: {dst}  age={int(age_sec)}s  lines={lines}")
                return dst
            except Exception as e:
                print(f"[cookies] copy failed {c} -> /tmp/cookies.txt : {e}")
    print("[cookies] not found")
    return None

def make_ydl(cookiefile: str | None, for_meta: bool = False) -> YoutubeDL:
    """
    YouTube anti-bot'u azaltmak için client fallback ve user-agent ekliyoruz.
    Cookie varsa onu kullanıyoruz, yoksa tv/android client ile şansımızı arttırıyoruz.
    """
    postprocessors = []
    use_ff = shutil.which("ffmpeg") is not None
    if use_ff and not for_meta:
        postprocessors = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    ydl_opts: dict = {
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title).80s.%(ext)s"),
        "quiet": True,
        "noprogress": False,
        "ignoreerrors": False,
        "retries": 3,
        "fragment_retries": 3,
        "nocheckcertificate": True,
        "http_headers": {
            # web’e benzeyen header
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36"),
            "Accept-Language": "en-US,en;q=0.9",
        },
        # YouTube için client fallback: önce TV/Android sonra web
        "extractor_args": {
            "youtube": {
                "player_client": ["tv", "android", "web"],
                # Bazı bölgelerde yararlı olur:
                "skip": ["configs"],
            }
        },
        # TR’de geo engelde bazen yardımcı olur:
        "geo_bypass_country": "US",
        "nopart": True,
        "postprocessors": postprocessors,
    }

    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile
        # cookie olduğunda web client de sorunsuz olsun diye ilk sıraya ‘web’i de ekleyebiliriz
        ydl_opts["extractor_args"]["youtube"]["player_client"] = ["web", "tv", "android"]

    return YoutubeDL(ydl_opts)

INDEX_HTML = """
<!doctype html>
<title>YouTube MP3 İndirici</title>
<h3>🎵 YouTube MP3 İndirici</h3>
<form method="get">
  <input style="width:520px" name="u" placeholder="https://www.youtube.com/watch?v=..." value="{{u or ''}}">
  <button>MP3'e Dönüştür</button>
</form>
{% if err %}<p style="color:#b00">❌ {{err}}</p>{% endif %}
{% if fn %}<p>✅ Hazır: <a href="{{ url_for('file', name=fn) }}">{{ fn }}</a></p>{% endif %}
<p style="font-size:12px;color:#777">Not: FFmpeg varsa MP3'e dönüştürülür; yoksa orijinal ses indirilir.</p>
"""

@app.route("/")
def index():
    url = request.args.get("u", "").strip()
    if not url:
        return render_template_string(INDEX_HTML, u=url)

    cookiefile = pick_cookiefile()

    def run_dl(fmt: str):
        y = make_ydl(cookiefile, for_meta=False)
        print(f"[yt-dlp] download fmt={fmt} cookie={'yes' if cookiefile else 'no'}")
        return y.extract_info(url, download=True)

    try:
        # Önce bestaudio (mp4/m4a tercih eder) – cookie varsa genelde 1. denemede geçer
        info = run_dl("bestaudio/best")
    except Exception as e1:
        print("FIRST TRY FAILED:", e1)
        # İkinci deneme: tv/android fallback ile web itiraz ederse format seçimini esnet
        try:
            y2 = make_ydl(cookiefile, for_meta=True)
            meta = y2.extract_info(url, download=False)  # sadece meta
            # en iyi sesi bul
            a = next(
                (f for f in sorted(meta.get("formats", []), key=lambda x: (x.get("abr") or 0), reverse=True)
                 if f.get("acodec") != "none"), None)
            if not a:
                raise RuntimeError("No audio format found")
            fmt_id = a.get("format_id") or "bestaudio/best"
            print(f"[meta] picked format_id={fmt_id}")
            y3 = make_ydl(cookiefile, for_meta=False)
            info = y3.extract_info(url, download=True)
        except Exception as e2:
            print("SECOND TRY ERROR:\n ", e2)
            return render_template_string(INDEX_HTML, u=url,
                                          err=f"DownloadError: {e1} / {e2}")

    fn = info.get("requested_downloads", [{}])[0].get("filepath") \
        or info.get("requested_downloads", [{}])[0].get("filename")
    if not fn:
        # yt-dlp bazen farklı anahtar yazar
        fn = info.get("filepath") or info.get("filename")

    if not fn:
        return render_template_string(INDEX_HTML, u=url,
                                      err="İndirme başarılı ama dosya adı bulunamadı.")
    name = os.path.basename(fn)
    return render_template_string(INDEX_HTML, u=url, fn=name)

@app.route("/d/<path:name>")
def file(name):
    return send_from_directory(DOWNLOAD_DIR, name, as_attachment=True)

@app.route("/healthz")
def health():
    return "ok"
