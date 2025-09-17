# -*- coding: utf-8 -*-
"""
Render-friendly Flask app: YouTube → MP3 with yt-dlp

- Cookies: UI upload (/tmp/cookies.txt) or Render Secret File (/etc/secrets/cookies.txt)
- Player client uyumluluğu: player_client daima STRING ("web,android,tv") -> split() hatası yok
- Strateji sıraları: web / android / tv / ios kombinasyonları
- Format seçimi: önce probe, en iyi gerçek ses formatını seç, sonra indir
- MP3: FFmpeg varsa mp3'e çevirir; yoksa orijinal ses uzantısı kalır
- Proxy: YTDLP_PROXY/HTTPS_PROXY/HTTP_PROXY/PROXY desteği
- PRG: Başarılı indirme sonrası redirect → form temiz gelir
"""

import os
import shutil
from typing import Optional, Dict, Any, List, Tuple

from flask import (
    Flask, request, send_from_directory, render_template_string,
    jsonify, redirect, url_for
)
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

DOWNLOAD_DIR = os.path.abspath(os.environ.get("DOWNLOAD_DIR", "/var/data"))
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Proxy env
PROXY = (
    os.environ.get("YTDLP_PROXY")
    or os.environ.get("HTTPS_PROXY")
    or os.environ.get("HTTP_PROXY")
    or os.environ.get("PROXY")
)

HTML = r"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>YouTube → MP3</title>
  <style>
    body{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;max-width:780px;margin:32px auto;padding:0 16px;line-height:1.5}
    input[type=text]{width:100%;padding:12px;border:1px solid #bbb;border-radius:10px}
    .row{display:flex;gap:8px;align-items:center;margin-top:12px}
    input[type=file]{flex:1}
    button{padding:10px 16px;border:0;border-radius:10px;background:#000;color:#fff;cursor:pointer}
    .msg{margin-top:14px;white-space:pre-wrap}
    a.btn{display:inline-block;margin-top:8px;padding:8px 12px;background:#0a7;color:#fff;border-radius:8px;text-decoration:none}
    .note{margin-top:16px;font-size:.95em;color:#777}
    code{background:#eee;padding:1px 5px;border-radius:6px}
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
    Not: FFmpeg varsa MP3'e dönüştürülür; yoksa m4a/webm kalır. Yalnızca hak sahibi olduğunuz içerikleri indirin.
  </div>
</body>
</html>
"""

app = Flask(__name__)

# ---------- helpers ----------

def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None

def ensure_cookiefile() -> Optional[str]:
    tmp = "/tmp/cookies.txt"
    if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        print("[cookie] using /tmp/cookies.txt")
        return tmp
    candidates = [
        os.environ.get("YTDLP_COOKIES"),
        "/etc/secrets/cookies.txt",
        "/etc/secrets/COOKIES.txt",
        "/etc/secrets/youtube-cookies.txt",
        "/app/cookies.txt",
    ]
    for src in candidates:
        if src and os.path.exists(src) and os.path.getsize(src) > 0:
            shutil.copyfile(src, tmp)
            print(f"[cookie] copied {src} -> {tmp}")
            return tmp
    print("[cookie] not found")
    return None

def build_opts(*, player_clients: str, cookiefile: Optional[str] = None, proxy: Optional[str] = PROXY, postprocess: bool = True) -> Dict[str, Any]:
    """
    Build yt-dlp options. 'player_clients' MUST be a comma-separated string like "web,android,tv".
    Using keyword-only args prevents accidental positional misuse.
    """
    assert isinstance(player_clients, str), "player_clients must be a comma-separated string"
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
        "socket_timeout": 30,
        "http_chunk_size": 1048576,  # 1MB
        "source_address": "0.0.0.0",
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        "extractor_args": {
            "youtube": {
                "player_client": player_clients,  # STRING -> yt-dlp can split internally
                "skip": ["configs"],
            }
        },
        "geo_bypass_country": "TR",
    }
    if proxy:
        opts["proxy"] = proxy
    if cookiefile:
        opts["cookiefile"] = cookiefile
    if postprocess and ffmpeg_available():
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    return opts

def choose_format(info: Dict[str, Any]) -> str:
    fmts = info.get("formats") or []
    if not fmts:
        return "bestaudio/best"
    candidates: List[Tuple[float, Dict[str, Any]]] = []
    for f in fmts:
        acodec = f.get("acodec")
        vcodec = f.get("vcodec")
        if not acodec or acodec == "none":
            continue
        is_audio_only = (vcodec in (None, "none"))
        abr = f.get("abr") or f.get("tbr") or 0
        ext = (f.get("ext") or "").lower()
        ext_bonus = 20 if ext == "m4a" else (10 if ext == "webm" else 0)
        score = (abr or 0) + (60 if is_audio_only else 0) + ext_bonus
        candidates.append((score, f))
    if not candidates:
        return "bestaudio/best"
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1].get("format_id") or "bestaudio/best"

# ---------- core ----------

def run_download(url: str) -> str:
    if not url:
        raise ValueError("URL boş olamaz.")
    cookie = ensure_cookiefile()

    strategies = [
        ("Cookie + Web Client",      "web" if cookie else "web,android,tv"),
        ("Mobile Clients",           "android,web,tv"),
        ("TV Client Bypass",         "tv,android,web"),
        ("iOS Client Only",          "ios"),
        ("All Clients Bypass",       "web,android,tv,ios"),
    ]

    last_err = None

    for idx, (name, clients_str) in enumerate(strategies, start=1):
        print(f"Strateji {idx}/{len(strategies)}: {name} -> {clients_str}")
        try:
            # 1) Probe
            opts_probe = build_opts(player_clients=clients_str, cookiefile=cookie, postprocess=False)
            with YoutubeDL(opts_probe) as y1:
                info = y1.extract_info(url, download=False)
                if info.get("is_live"):
                    raise DownloadError("Canlı yayın desteklenmiyor.")
                fmt = choose_format(info)

            # 2) Download
            opts_dl = build_opts(player_clients=clients_str, cookiefile=cookie, postprocess=True)
            opts_dl["format"] = fmt

            before = set(os.listdir(DOWNLOAD_DIR))
            with YoutubeDL(opts_dl) as y2:
                y2.download([url])
            after = set(os.listdir(DOWNLOAD_DIR))
            new_files = sorted(after - before, key=lambda f: os.path.getmtime(os.path.join(DOWNLOAD_DIR, f)), reverse=True)
            if new_files:
                return new_files[0]

            # rare fallback
            title = (info.get("title") or "audio").strip()
            ext = "mp3" if ffmpeg_available() else (info.get("ext") or "m4a")
            safe = "".join(c for c in f"{title}.{ext}" if c not in "\\/:*?\"<>|").strip()
            return safe

        except Exception as e:
            last_err = e
            print(f"❌ Strateji {idx} başarısız: {e}")

    # all failed
    msg = str(last_err) if last_err else "Bilinmeyen hata"
    low = msg.lower()
    hint = ""
    if ("sign in to confirm you're not a bot" in low) or ("bot olmadığınızı" in low):
        hint = ("\nİpucu: cookies.txt'yi güncelleyin (oturum açık profilden export) "
                "ve gerekiyorsa YTDLP_PROXY ile residential proxy tanımlayın.")
    raise RuntimeError(f"Tüm anti-bot stratejileri başarısız: {msg}{hint}")

# ---------- flask (PRG) ----------

@app.get("/health")
def health():
    return jsonify(ok=True)

@app.route("/", methods=["GET", "POST"])
def index():
    # GET: form temiz gelsin
    if request.method == "GET":
        return render_template_string(HTML, msg=None, filename=None, url="")

    # POST: indir, sonra /done'a redirect (form sıfırlansın)
    url = (request.form.get("url") or "").strip()
    up = request.files.get("cookies")
    if up and up.filename:
        up.save("/tmp/cookies.txt")
        print("[cookie] uploaded -> /tmp/cookies.txt")

    try:
        filename = run_download(url)
        return redirect(url_for("done", filename=filename))
    except Exception as e:
        msg = f"❌ İndirme Hatası: {e}"
        # Hata varsa formu girilen URL ile tekrar göster (debug için)
        return render_template_string(HTML, msg=msg, filename=None, url=url), 400

@app.get("/done")
def done():
    filename = request.args.get("filename")
    msg = "✅ İndirme tamamlandı."
    # Form boş, sadece mesaj + indirme butonu
    return render_template_string(HTML, msg=msg, filename=filename, url="")

@app.route("/download/<path:filename>")
def download(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
