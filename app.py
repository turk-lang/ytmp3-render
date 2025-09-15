# -*- coding: utf-8 -*-
"""
Render-friendly Flask app: YouTube → MP3 with yt-dlp

- Cookies:
  * Upload via UI (saved to /tmp/cookies.txt), OR
  * Mount as Secret File at /etc/secrets/cookies.txt
- Format selection:
  * Probe available formats, pick a real audio format_id, then download
- Fallbacks:
  * Try multiple YouTube player_client orders (web/android/tv)
- MP3:
  * Uses FFmpeg if available; otherwise leaves original audio (m4a/webm)

LEGAL: Only download content you have rights to. Respect YouTube ToS and local laws.
"""
import os
import shutil
from typing import Optional, Dict, Any, List, Tuple

from flask import Flask, request, send_from_directory, render_template_string, jsonify
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# ---------------------------- CONFIG ----------------------------

DOWNLOAD_DIR = os.path.abspath(os.environ.get("DOWNLOAD_DIR", "/var/data"))
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

PROXY = (
    os.environ.get("YTDLP_PROXY")
    or os.environ.get("HTTPS_PROXY")
    or os.environ.get("HTTP_PROXY")
    or os.environ.get("PROXY")
)

# ---------------------------- HTML ------------------------------

HTML = r"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>YouTube → MP3</title>
  <style>
    :root { color-scheme: light dark; }
    body{font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
         max-width: 780px; margin: 32px auto; padding: 0 16px; line-height: 1.5}
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
  <h2>YouTube → MP3</h2>
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

# ---------------------------- UTILS -----------------------------

def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None

def ensure_cookiefile() -> Optional[str]:
    """Return a path to cookies.txt if present; prefer /tmp, else secret file."""
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

def common_opts(client_order: List[str], cookiefile: Optional[str]) -> Dict[str, Any]:
    """Build a safe yt-dlp options dict without 'format' (set later)."""
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
        "source_address": "0.0.0.0",  # IPv4
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        "extractor_args": {
            "youtube": {
                "player_client": client_order,
                "skip": ["configs"],
            }
        },
        "geo_bypass_country": "TR",
    }
    if PROXY:
        opts["proxy"] = PROXY
    if cookiefile:
        opts["cookiefile"] = cookiefile
    if ffmpeg_available():
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    return opts

def choose_format(info: Dict[str, Any]) -> str:
    """Pick an actually available audio format_id. Prefer audio-only m4a with higher bitrate."""
    fmts = info.get("formats") or []
    candidates: List[Tuple[float, Dict[str, Any]]] = []
    for f in fmts:
        acodec = f.get("acodec")
        vcodec = f.get("vcodec")
        if not acodec or acodec == "none":
            continue
        is_audio_only = (vcodec in (None, "none"))
        abr = f.get("abr") or f.get("tbr") or 0
        ext = (f.get("ext") or "").lower()
        score = (abr or 0) + (50 if is_audio_only else 0) + (10 if ext == "m4a" else 0)
        candidates.append((score, f))
    if not candidates:
        # Fallback: yt-dlp will still try best; may be live/protected content
        return "bestaudio/best"
    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0][1]
    fmt_id = best.get("format_id")
    return fmt_id or "bestaudio/best"

def run_download(url: str) -> str:
    if not url:
        raise ValueError("URL boş olamaz.")

    cookie = ensure_cookiefile()

    client_orders = []
    if cookie:
        client_orders = [
            ["web", "android", "tv"],
            ["android", "web", "tv"],
            ["ios", "android", "tv", "web"],
        ]
    else:
        client_orders = [
            ["android", "tv", "web"],
            ["web", "android", "tv"],
            ["ios", "android", "tv", "web"],
        ]

    last_err = None
    for order in client_orders:
        try:
            opts_probe = common_opts(order, cookie)
            # Probe available formats
            with YoutubeDL(opts_probe) as y1:
                info = y1.extract_info(url, download=False)
                if info.get("is_live"):
                    raise DownloadError("Canlı yayınlar için indirme desteklenmiyor.")
                fmt = choose_format(info)

            # Now download with chosen format
            opts_dl = dict(opts_probe)
            opts_dl["format"] = fmt

            before = set(os.listdir(DOWNLOAD_DIR))
            with YoutubeDL(opts_dl) as y2:
                y2.download([url])
            after = set(os.listdir(DOWNLOAD_DIR))
            new_files = sorted(after - before, key=lambda f: os.path.getmtime(os.path.join(DOWNLOAD_DIR, f)), reverse=True)
            if new_files:
                return new_files[0]
            # Fallback: construct name from title/ext if needed
            title = (info.get("title") or "audio").strip()
            ext = "mp3" if ffmpeg_available() else (info.get("ext") or "m4a")
            safe = "".join(c for c in f"{title}.{ext}" if c not in "\\/:*?\"<>|").strip()
            return safe
        except Exception as e:
            last_err = e
            print(f"[retry] order={order} error={e}")

    # If we reach here, all attempts failed
    err_msg = str(last_err) if last_err else "Bilinmeyen hata"
    # Add guidance for bot verification cases
    low = err_msg.lower()
    hint = ""
    if ("sign in to confirm you're not a bot" in low) or ("bot olmadığınızı" in low):
        hint = (
            "\nİpucu: Geçerli bir cookies.txt ekleyin (YouTube hesabınızda oturum açıp çıkarın) "
            "veya residential bir proxy ayarlayın (YTDLP_PROXY)."
        )
    raise RuntimeError(err_msg + hint)

# ---------------------------- APP -------------------------------

app = Flask(__name__)

@app.get("/health")
def health():
    return jsonify(ok=True)

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
            hint = ""
            low = (err_txt or "").lower()
            if ("sign in to confirm you're not a bot" in low) or ("bot olmadığınızı" in low):
                hint = (
                    "<br><b>Öneri:</b> Tarayıcıda YouTube oturumu açıkken "
                    "<i>cookies.txt</i> çıkarıp buradan yükleyin <i>veya</i> "
                    "Render'da Secret File olarak <code>/etc/secrets/cookies.txt</code> ekleyin. "
                    "Gerekirse <code>YTDLP_PROXY</code> kullanın."
                )
            msg = f"❌ İndirme Hatası: {err_txt}{hint}"

    return render_template_string(HTML, msg=msg, filename=filename, url=url)

@app.route("/download/<path:filename>")
def download(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
