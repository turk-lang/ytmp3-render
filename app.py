import os
import re
import time
import shutil
from flask import Flask, request, render_template_string, send_file
from yt_dlp import YoutubeDL

app = Flask(__name__)

# İndirme klasörü
DOWNLOAD_DIR = "/tmp"

HTML = """
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <title>🎵 YouTube → MP3 Dönüştürücü</title>
  <style>
    body{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:760px;margin:32px auto;padding:0 16px}
    input[type=text]{width:100%;padding:12px;border:1px solid #ccc;border-radius:8px}
    button{margin-top:10px;padding:10px 16px;border:0;border-radius:8px;background:#111;color:#fff;cursor:pointer}
    .msg{margin-top:14px}
    a.btn{display:inline-block;margin-top:8px;padding:8px 12px;background:#0a7; color:#fff;border-radius:8px;text-decoration:none}
    small{color:#777}
  </style>
</head>
<body>
  <h2>🎵 YouTube → MP3</h2>
  <form method="post">
    <input type="text" name="url" placeholder="https://www.youtube.com/watch?v=..." value="{{url or ''}}" required>
    <button type="submit">İndir</button>
  </form>
  {% if msg %}<div class="msg">{{ msg|safe }}</div>{% endif %}
  {% if filename %}
    <p class="msg">✅ Hazır: <a class="btn" href="/download/{{ filename }}">Dosyayı indir</a></p>
  {% endif %}
  <p><small>Not: FFmpeg varsa MP3'e dönüştürülür; yoksa orijinal ses uzantısı (m4a/webm) kalır.</small></p>
</body>
</html>
"""

# --- Cookie'yi Secret File'dan /tmp/cookies.txt'ye kopyala
def ensure_cookiefile() -> str | None:
    """
    Render Secret Files (veya ENV) içindeki cookies.txt'yi /tmp/cookies.txt'ye kopyalar.
    Varsa yolunu döndürür; yoksa None.
    """
    candidates = [
        os.environ.get("YTDLP_COOKIES"),
        "/etc/secrets/cookies.txt",
        "/etc/secrets/COOKIES.txt",
        "/etc/secrets/youtube-cookies.txt",
        "/app/cookies.txt",  # lokal geliştirme
    ]
    for src in candidates:
        if not src:
            continue
        try:
            if os.path.exists(src) and os.path.getsize(src) > 0:
                dst = "/tmp/cookies.txt"
                # değiştiyse kopyala
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

def base_opts():
    cookie = ensure_cookiefile()
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
        "source_address": "0.0.0.0",  # IPv4
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
        "extractor_args": {
            "youtube": {
                # cookie varsa web'i öne al; yoksa tv/android ile başla (anti-bot azaltır)
                "player_client": ["web", "tv", "android"] if cookie else ["tv", "android", "web"],
                "skip": ["configs"],
            }
        },
        "geo_bypass_country": "US",
    }
    if cookie:
        opts["cookiefile"] = cookie
    return opts

def extract_meta(url: str):
    opts = base_opts()
    opts["skip_download"] = True
    with YoutubeDL(opts) as y:
        return y.extract_info(url, download=False)

def pick_format(meta: dict) -> str:
    """
    Öncelik: m4a (audio-only) > opus/webm (audio-only) > başka audio-only (en yüksek abr) > best
    Her format ID videodan videoya değişebildiği için sabit ID'lere güvenmiyoruz.
    """
    fmts = (meta or {}).get("formats") or []
    # Audio-only olanlar
    audio_only = [f for f in fmts if (f.get("vcodec") in (None, "none")) and (f.get("acodec") not in (None, "none"))]

    # 1) m4a
    m4a = [f for f in audio_only if (f.get("ext") or "").lower() == "m4a"]
    if m4a:
        return m4a[0].get("format_id")

    # 2) opus/webm
    opus = [f for f in audio_only if (f.get("ext") or "").lower() in ("webm", "opus")]
    if opus:
        return opus[0].get("format_id")

    # 3) başka audio-only: en yüksek abr/tbr
    if audio_only:
        audio_only.sort(key=lambda f: float(f.get("abr") or f.get("tbr") or 0), reverse=True)
        return audio_only[0].get("format_id")

    # 4) hiçbiri yoksa best (mux)
    return "best"

def run_download(url: str, fmt: str | None):
    opts = base_opts()
    use_ff = shutil.which("ffmpeg") is not None
    if fmt:
        opts["format"] = fmt
    if use_ff:
        opts.update({
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "postprocessor_args": ["-ar", "44100"],
            "prefer_ffmpeg": True,
        })
    with YoutubeDL(opts) as y:
        return y.extract_info(url, download=True)

def safe_name(title: str, ext: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]', "_", title or "audio")
    return f"{safe}.{ext}"

@app.route("/", methods=["GET", "POST"])
def index():
    msg = None
    filename = None
    url = ""
    if request.method == "POST":
        url = (request.form.get("url") or "").strip()
        if not url:
            msg = "❌ URL gerekli"
        else:
            try:
                # 1) Meta çek & format seç
                meta = extract_meta(url)
                fmt_id = pick_format(meta)
                print(f"[format] seçilen fmt_id={fmt_id}")

                # 2) İndir (seçilen formatla)
                info = run_download(url, fmt_id)
            except Exception as e1:
                print("PRIMARY DOWNLOAD FAILED:", e1)
                try:
                    # 3) Serbest bırak (yt-dlp kendi seçsin)
                    info = run_download(url, None)
                except Exception as e2:
                    import traceback
                    print("SECOND TRY ERROR:\n", traceback.format_exc())
                    msg = f"❌ İndirme Hatası: {e1} / {e2}"
                    return render_template_string(HTML, msg=msg, filename=None, url=url)

            # Dosya adını tahmin et
            use_ff = shutil.which("ffmpeg") is not None
            title = info.get("title") or "audio"
            ext = "mp3" if use_ff else (info.get("ext") or "m4a")
            filename = safe_name(title, ext)
            path = os.path.join(DOWNLOAD_DIR, filename)

            # Bazı sürümlerde requested_downloads içerir
            if not os.path.exists(path):
                reqs = info.get("requested_downloads") or []
                if reqs:
                    fp = reqs[0].get("filepath") or reqs[0].get("filename")
                    if fp and os.path.exists(fp):
                        # ffmpeg yoksa gelen uzantıyı kullan
                        ext2 = os.path.splitext(fp)[1]
                        if not use_ff and ext2:
                            filename = safe_name(title, ext2.lstrip("."))
                            path = os.path.join(DOWNLOAD_DIR, filename)
                        try:
                            if fp != path:
                                shutil.move(fp, path)
                        except Exception:
                            pass

            if os.path.exists(path):
                msg = "✅ Dönüştürme tamam!"
            else:
                msg = "⚠️ İndirme başarılı görünüyor ancak dosya bulunamadı."

    return render_template_string(HTML, msg=msg, filename=filename, url=url)

@app.route("/download/<path:filename>")
def download(filename):
    return send_file(os.path.join(DOWNLOAD_DIR, filename), as_attachment=True)

@app.route("/healthz")
def healthz():
    return "ok"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
