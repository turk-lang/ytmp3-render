# -*- coding: utf-8 -*-
"""
A minimal Flask-based YouTube → MP3 downloader using yt-dlp.

Features:
- Handles YouTube's "sign in to confirm you're not a bot" by letting the user upload cookies.txt.
- Also *attempts* to pull cookies from a local browser (Chrome/Firefox) if available (for local desktop usage).
- Converts to MP3 when FFmpeg is installed; otherwise leaves the original audio format (m4a/webm).
- Clean UI with a file input to upload cookies and a link to download the finalized file.

⚠ Legal note: Only download content you have rights to. Respect YouTube Terms of Service and local laws.
"""

"""
Requirements:
    pip install Flask yt-dlp

Run:
    BROWSER=chrome python app.py
or simply:
    python app.py

Then open http://127.0.0.1:5000/
"""
import os
import shutil
import traceback
from typing import Optional, Dict, Any

from flask import Flask, request, send_from_directory, render_template_string
from yt_dlp import YoutubeDL

# ---------------------------- Config ----------------------------

APP_TITLE = "🎵 YouTube → MP3"
DOWNLOAD_DIR = os.path.abspath(os.environ.get("DOWNLOAD_DIR", "/mnt/data/downloads"))
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ---------------------------- HTML -----------------------------

HTML = r"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>🎵 YouTube → MP3</title>
  <style>
    :root { color-scheme: light dark; }
    body{font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; 
         max-width: 780px; margin: 32px auto; padding: 0 16px; line-height: 1.45}
    h2{margin-bottom: 12px}
    form{display:block; margin-top: 8px}
    input[type=text]{width:100%; padding:12px; border:1px solid #bbb; border-radius:10px}
    .row{display:flex; gap:8px; align-items:center; margin-top:12px}
    input[type=file]{flex:1}
    button{padding:10px 16px; border:0; border-radius:10px; background:#000; color:#fff; cursor:pointer}
    .msg{margin-top:14px; white-space:pre-wrap}
    a.btn{display:inline-block; margin-top:8px; padding:8px 12px; background:#0a7; color:#fff; border-radius:8px; text-decoration:none}
    small{color:#777}
    .note{margin-top:16px; font-size:.95em}
    code{background:#eee; padding:1px 5px; border-radius:6px}
  </style>
</head>
<body>
  <h2>🎵 YouTube → MP3</h2>
  <form method="post" enctype="multipart/form-data">
    <input type="text" name="url" placeholder="https://www.youtube.com/watch?v=..." value="{{url or ''}}" required>
    <div class="row">
      <input type="file" name="cookies" accept=".txt">
      <button type="submit">İndir</button>
    </div>
  </form>
  {% if msg %}<div class="msg">{{ msg|safe }}</div>{% endif %}
  {% if filename %}
    <p class="msg">✅ Hazır: <a class="btn" href="/download/{{ filename }}">Dosyayı indir</a></p>
  {% endif %}
  <div class="note">
    <small>Not: FFmpeg varsa MP3'e dönüştürülür; yoksa orijinal ses uzantısı (m4a/webm) kalır.</small><br>
    <small>⚠ Yalnızca hak sahibi olduğunuz içerikleri indirin. YouTube kullanım şartlarına uyun.</small>
  </div>
</body>
</html>
"""

# ---------------------------- Utils ----------------------------

def ensure_cookiefile() -> Optional[str]:
    """
    Returns a path to a cookies.txt file if available.
    Precedence:
      1) /tmp/cookies.txt (uploaded by user via UI)
      2) $YTDLP_COOKIES, /etc/secrets/*.txt, /app/cookies.txt (container / server)
    """
    tmp = "/tmp/cookies.txt"
    try:
        if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            print(f"[cookies] using existing: {tmp}")
            return tmp
    except Exception as e:
        print(f"[cookies] check /tmp failed: {e}")

    candidates = [
        os.environ.get("YTDLP_COOKIES"),
        "/etc/secrets/cookies.txt",
        "/etc/secrets/COOKIES.txt",
        "/etc/secrets/youtube-cookies.txt",
        "/app/cookies.txt",
    ]
    for src in candidates:
        if not src:
            continue
        try:
            if os.path.exists(src) and os.path.getsize(src) > 0:
                # Copy into /tmp to unify downstream path usage
                shutil.copyfile(src, tmp)
                print(f"[cookies] copied {src} -> {tmp}")
                return tmp
        except Exception as e:
            print(f"[cookies] copy failed {src} -> {tmp} : {e}")

    print("[cookies] not found")
    return None


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def base_opts() -> Dict[str, Any]:
    """
    Build a base options dict for YoutubeDL.
    - Prefers cookiefile if available; otherwise tries cookies-from-browser (local desktop runs).
    - Tunes player_client order to mitigate bot checks.
    """
    cookie = ensure_cookiefile()

    # Try pulling cookies from a local browser (for LOCAL desktop usage)
    cookies_from_browser = None
    browser = (os.environ.get("BROWSER") or "").lower()
    if browser in ("chrome", "chromium", "edge", "brave", "vivaldi"):
        cookies_from_browser = ("chrome", None, None, None)
    elif browser == "firefox":
        cookies_from_browser = ("firefox", None, None, None)
    else:
        # Attempt Chrome by default; yt-dlp will skip if unavailable
        cookies_from_browser = ("chrome", None, None, None)

    opts: Dict[str, Any] = {
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title).90s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "cachedir": False,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "nocheckcertificate": True,
        "source_address": "0.0.0.0",  # prefer IPv4
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
        "extractor_args": {
            "youtube": {
                # If we have a cookie file, try web first; otherwise start with tv/android (less bot checks)
                "player_client": ["web", "tv", "android"] if cookie else ["tv", "android", "web"],
                "skip": ["configs"],
            }
        },
        "geo_bypass_country": "US",
    }

    if cookie:
        opts["cookiefile"] = cookie
    else:
        opts["cookiesfrombrowser"] = cookies_frombrowser

    # Post-process to MP3 when FFmpeg exists; else just grab bestaudio
    if ffmpeg_available():
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            # Keep both? No—by default it removes source after conversion
        })
    else:
        opts.update({"format": "bestaudio/best"})

    return opts


def run_download(url: str) -> str:
    """
    Executes yt-dlp download. Returns the final filename (basename only).
    If MP3 conversion is enabled, extension will be .mp3.
    """
    if not url:
        raise ValueError("URL boş olamaz.")

    opts = base_opts()

    before = set(os.listdir(DOWNLOAD_DIR))
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # Detect newly created/modified files
        after = set(os.listdir(DOWNLOAD_DIR))
        new_files = sorted(list(after - before))
        if new_files:
            # Return the newest file (in case multiple artifacts appear)
            new_files.sort(key=lambda f: os.path.getmtime(os.path.join(DOWNLOAD_DIR, f)), reverse=True)
            return new_files[0]

        # Fallback (should rarely happen)
        title = info.get("title") or "audio"
        ext = "mp3" if ffmpeg_available() else (info.get("ext") or "m4a")
        filename = f"{title}.{ext}"
        safe = "".join(c for c in filename if c not in "\\/:*?\"<>|").strip()
        return safe


# ---------------------------- Flask App ----------------------------

app = Flask(__name__)


@app.route("/", methods=["GET", "POST"])
def index():
    msg = None
    filename = None
    url = ""

    if request.method == "POST":
        url = (request.form.get("url") or "").strip()

        # Handle uploaded cookies.txt (optional)
        file = request.files.get("cookies")
        if file and file.filename:
            try:
                upath = "/tmp/cookies.txt"
                file.save(upath)
                print(f"[cookies] uploaded -> {upath} size={os.path.getsize(upath)}")
            except Exception as ue:
                print(f"[cookies] upload failed: {ue}")

        try:
            final_name = run_download(url)
            filename = final_name
            msg = "✅ İndirme başarıyla tamamlandı."
        except Exception as e1:
            err_txt = str(e1)
            print("PRIMARY DOWNLOAD FAILED:", err_txt)
            print(traceback.format_exc())

            # Friendly hint when YouTube wants sign-in/bot check
            hint = ""
            lower = (err_txt or "").lower()
            if ("sign in to confirm you're not a bot" in lower) or ("bot olmadığınızı" in lower):
                hint = (
                    "<br><b>Öneri:</b> Tarayıcıda YouTube oturumu açıkken "
                    "<i>cookies.txt</i> çıkarıp buradan yükleyin ya da uygulamayı "
                    "<code>BROWSER=chrome</code> şeklinde çalıştırın."
                )

            msg = f"❌ İndirme Hatası: {err_txt}{hint}"

    return render_template_string(HTML, msg=msg, filename=filename, url=url)


@app.route("/download/<path:filename>")
def download(filename):
    # Security: serve only from DOWNLOAD_DIR
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
