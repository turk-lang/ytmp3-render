# -*- coding: utf-8 -*-
"""
Flask-based YouTube → MP3 downloader using yt-dlp, tuned for Render.com.

- Accepts cookies.txt upload (saved to /tmp/cookies.txt)
- Auto-loads a secret file at /etc/secrets/cookies.txt if present (Render Secret File)
- FFmpeg-based MP3 conversion when available (Docker installs ffmpeg)
- Fallback retry with Android-first player_client to help bypass bot checks
"""

import os
import shutil
import traceback
from typing import Optional, Dict, Any

from flask import Flask, request, send_from_directory, render_template_string
from yt_dlp import YoutubeDL

APP_TITLE = "🎵 YouTube → MP3"
DOWNLOAD_DIR = os.path.abspath(os.environ.get("DOWNLOAD_DIR", "/var/data"))
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

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

def ensure_cookiefile() -> Optional[str]:
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
                shutil.copyfile(src, tmp)
                print(f"[cookies] copied {src} -> {tmp}")
                return tmp
        except Exception as e:
            print(f"[cookies] copy failed {src} -> {tmp} : {e}")

    print("[cookies] not found")
    return None

def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None

def common_opts() -> Dict[str, Any]:
    return {
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
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
        "geo_bypass_country": "US",
    }

def opts_primary() -> Dict[str, Any]:
    cookie = ensure_cookiefile()
    opts = common_opts()
    opts["extractor_args"] = {
        "youtube": {
            "player_client": ["web", "tv", "android"] if cookie else ["tv", "android", "web"],
            "skip": ["configs"],
        }
    }
    if cookie:
        opts["cookiefile"] = cookie
    return attach_postprocessor(opts)

def opts_fallback_android_first() -> Dict[str, Any]:
    cookie = ensure_cookiefile()
    opts = common_opts()
    opts["extractor_args"] = {
        "youtube": {
            "player_client": ["android", "tv", "web"],
            "skip": ["configs"],
        }
    }
    if cookie:
        opts["cookiefile"] = cookie
    return attach_postprocessor(opts)

def attach_postprocessor(opts: Dict[str, Any]) -> Dict[str, Any]:
    if ffmpeg_available():
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
    else:
        opts.update({"format": "bestaudio/best"})
    return opts

def run_download(url: str) -> str:
    if not url:
        raise ValueError("URL boş olamaz.")

    def _download_with(opts: Dict[str, Any]) -> str:
        before = set(os.listdir(DOWNLOAD_DIR))
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            after = set(os.listdir(DOWNLOAD_DIR))
            new_files = sorted(list(after - before))
            if new_files:
                new_files.sort(key=lambda f: os.path.getmtime(os.path.join(DOWNLOAD_DIR, f)), reverse=True)
                return new_files[0]
            # Fallback name (rare)
            title = info.get("title") or "audio"
            ext = "mp3" if ffmpeg_available() else (info.get("ext") or "m4a")
            return "".join(c for c in f"{title}.{ext}" if c not in "\\/:*?\"<>|").strip()

    # Try primary
    try:
        return _download_with(opts_primary())
    except Exception as e1:
        msg = str(e1).lower()
        print("PRIMARY FAILED:", e1)
        print(traceback.format_exc())

        bot_hint = ("sign in to confirm you're not a bot" in msg) or ("bot olmadığınızı" in msg)

        # Fallback: Android-first order (works for some bot-check cases)
        try:
            return _download_with(opts_fallback_android_first())
        except Exception as e2:
            print("FALLBACK FAILED:", e2)
            print(traceback.format_exc())
            if bot_hint:
                raise RuntimeError(
                    f"{e1}\n\nİpucu: cookies.txt yükleyin (YouTube'da giriş yapıp çerezleri alın) "
                    f"veya Render'da /etc/secrets/cookies.txt olarak Secret File ekleyin."
                )
            raise e2

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    msg = None
    filename = None
    url = ""

    if request.method == "POST":
        url = (request.form.get("url") or "").strip()

        # Optional cookies.txt upload via UI
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
            print("DOWNLOAD ERROR:", err_txt)
            print(traceback.format_exc())
            hint = ""
            lower = (err_txt or "").lower()
            if ("sign in to confirm you're not a bot" in lower) or ("bot olmadığınızı" in lower):
                hint = (
                    "<br><b>Öneri:</b> Tarayıcıda YouTube oturumu açıkken "
                    "<i>cookies.txt</i> çıkarıp buradan yükleyin <i>veya</i> "
                    "Render'da Secret File olarak <code>/etc/secrets/cookies.txt</code> ekleyin."
                )
            msg = f"❌ İndirme Hatası: {err_txt}{hint}"

    return render_template_string(HTML, msg=msg, filename=filename, url=url)

@app.route("/download/<path:filename>")
def download(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
