# -*- coding: utf-8 -*-
"""
YouTube → MP3 (Render-friendly) — Stabil Sürüm
- Cookie varsa: tek strateji (web)
- Cookie yoksa: android → web
- player_client her zaman STRING (liste gelirse join)
- Anti-bot hatasında kısa bekleme (3 sn)
- Başarılı indirme → /done sayfası (İndir butonu); butona tıklayınca dosya iner ve otomatik / sayfasına döner (form sıfırlanır)
- /cookie_check: cookie sağlığı
"""

import os
import re
import time
import shutil
from typing import Optional, Dict, Any, List, Tuple

from flask import Flask, request, send_from_directory, render_template_string, jsonify, redirect, url_for
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# --------- Config ----------
DOWNLOAD_DIR = os.path.abspath(os.environ.get("DOWNLOAD_DIR", "/var/data"))
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

PROXY = (
    os.environ.get("YTDLP_PROXY")
    or os.environ.get("HTTPS_PROXY")
    or os.environ.get("HTTP_PROXY")
    or os.environ.get("PROXY")
)

# --------- HTML ---------
HTML_BASE = r"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>YouTube → MP3</title>
  <style>
    :root { --bg:#fff; --fg:#111; --muted:#6b7280; --primary:#111; --ok:#0a7; --err:#d32f2f; --okbg:#e8f5e8; --errbg:#ffebee; }
    body{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;max-width:780px;margin:32px auto;padding:0 16px;line-height:1.5;color:var(--fg);background:var(--bg)}
    h2{margin:8px 0 16px}
    input[type=text]{width:100%;padding:12px;border:1px solid #cbd5e1;border-radius:10px}
    .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px}
    input[type=file]{flex:1}
    button,a.btn{padding:10px 16px;border:0;border-radius:10px;background:var(--primary);color:#fff;cursor:pointer;text-decoration:none;display:inline-block}
    button[disabled],a.btn.disabled{opacity:.6;pointer-events:none}
    .msg{margin-top:14px;white-space:pre-wrap}
    .ok{background:var(--okbg);color:#14532d;padding:12px;border-radius:8px}
    .err{background:var(--errbg);color:var(--err);padding:12px;border-radius:8px}
    .note{margin-top:16px;font-size:.95em;color:var(--muted)}
    .divider{height:1px;background:#e5e7eb;margin:20px 0}
  </style>
</head>
<body>
  <h2>YouTube → MP3</h2>
  {% block content %}{% endblock %}
  <div class="note">
    Not: FFmpeg varsa MP3'e dönüştürülür; yoksa m4a/webm kalır. Yalnızca hak sahibi olduğunuz içerikleri indirin.
  </div>
</body>
</html>
"""

HTML_FORM = r"""{% extends none %}
{% block content %}
  <form method="post" enctype="multipart/form-data">
    <input type="text" name="url" placeholder="https://www.youtube.com/watch?v=..." value="{{url or ''}}" required>
    <div class="row">
      <input type="file" name="cookies" accept=".txt">
      <button type="submit">İndir</button>
    </div>
  </form>
  {% if msg %}<div class="msg {{ 'ok' if msg_ok else 'err' }}">{{ msg|safe }}</div>{% endif %}
{% endblock %}
"""

HTML_DONE = r"""{% extends none %}
{% block content %}
  <div class="msg ok">✅ İndirme tamamlandı.</div>
  <p style="margin-top:12px">
    <a id="dlbtn" class="btn" href="/download/{{ filename }}"
       onclick="this.textContent='İndiriliyor...'; this.classList.add('disabled'); setTimeout(function(){ window.location='{{ url_for('index') }}'; }, 1500);">
      📥 Dosyayı indir
    </a>
  </p>
  <div class="divider"></div>
  <form method="post" enctype="multipart/form-data">
    <input type="text" name="url" placeholder="Yeni link: https://www.youtube.com/watch?v=..." value="" required>
    <div class="row">
      <input type="file" name="cookies" accept=".txt">
      <button type="submit">Yeni İndirme</button>
    </div>
  </form>
{% endblock %}
"""

app = Flask(__name__)

# --------- Helpers ---------
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None

def is_valid_youtube_url(url: str) -> bool:
    pat = re.compile(
        r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|shorts/|.+\?v=)?([A-Za-z0-9_-]{11})'
    )
    return bool(pat.match(url or ""))

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

def build_opts(*, player_clients, cookiefile: Optional[str] = None, proxy: Optional[str] = PROXY, postprocess: bool = True) -> Dict[str, Any]:
    """player_clients: list[str] veya str kabul eder → stringe çevrilir."""
    if isinstance(player_clients, list):
        player_clients = ",".join(player_clients)  # ✅ split hatasını önler
    assert isinstance(player_clients, str), "player_clients string olmalı"

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
        "http_chunk_size": 1048576,
        "source_address": "0.0.0.0",
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        "extractor_args": {
            "youtube": {
                "player_client": player_clients,   # ✅ her zaman string
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
        acodec = f.get("acodec"); vcodec = f.get("vcodec")
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

# --------- Core ---------
def run_download(url: str) -> str:
    if not url:
        raise ValueError("URL boş olamaz.")
    if not is_valid_youtube_url(url):
        raise ValueError("Geçerli bir YouTube URL'si giriniz.")

    cookie = ensure_cookiefile()

    # Cookie varsa tek deneme (web). Yoksa 2 adım: android → web
    if cookie:
        strategies = [("Cookie + Web Client", ["web"])]
    else:
        strategies = [("Mobile Clients", ["android", "web", "tv"]),
                      ("Web Only", ["web"])]

    last_err = None
    for idx, (name, clients) in enumerate(strategies, start=1):
        print(f"Strateji {idx}/{len(strategies)}: {name} -> {','.join(clients) if isinstance(clients, list) else clients}")
        try:
            # 1) Probe
            opts_probe = build_opts(player_clients=clients, cookiefile=cookie, postprocess=False)
            with YoutubeDL(opts_probe) as y1:
                info = y1.extract_info(url, download=False)
                if info.get("is_live"):
                    raise DownloadError("Canlı yayın desteklenmiyor.")
                fmt = choose_format(info)

            # 2) Download
            opts_dl = build_opts(player_clients=clients, cookiefile=cookie, postprocess=True)
            opts_dl["format"] = fmt

            before = set(os.listdir(DOWNLOAD_DIR))
            with YoutubeDL(opts_dl) as y2:
                y2.download([url])
            after = set(os.listdir(DOWNLOAD_DIR))
            new_files = sorted(
                after - before,
                key=lambda f: os.path.getmtime(os.path.join(DOWNLOAD_DIR, f)),
                reverse=True
            )
            if new_files:
                return new_files[0]

            # Nadir durum: isim tahmini
            title = (info.get("title") or "audio").strip()
            ext = "mp3" if ffmpeg_available() else (info.get("ext") or "m4a")
            safe = "".join(c for c in f"{title}.{ext}" if c not in "\\/:*?\"<>|").strip()
            return safe

        except Exception as e:
            last_err = e
            print(f"❌ Strateji {idx} başarısız: {e}")
            low = str(e).lower()
            if ("sign in to confirm you're not a bot" in low) or ("bot olmadığınızı" in low):
                time.sleep(3)  # anti-bot baskısını yumuşat
            continue

    msg = str(last_err) if last_err else "Bilinmeyen hata"
    low = msg.lower()
    if ("sign in to confirm you're not a bot" in low) or ("bot olmadığınızı" in low):
        msg += "\nİpucu: güncel cookies.txt yükleyin ve/veya YTDLP_PROXY ile residential sticky proxy kullanın."
    raise RuntimeError(f"Tüm anti-bot stratejileri başarısız: {msg}")

# --------- Flask Routes ---------
@app.get("/health")
def health():
    return jsonify(ok=True, ffmpeg=ffmpeg_available(), download_dir=DOWNLOAD_DIR, proxy=bool(PROXY))

@app.get("/cookie_check")
def cookie_check():
    path = "/tmp/cookies.txt"
    if not os.path.exists(path):
        secret_path = "/etc/secrets/cookies.txt"
        if os.path.exists(secret_path):
            try:
                shutil.copyfile(secret_path, path)
            except Exception:
                pass
    if not os.path.exists(path):
        return jsonify(ok=False, reason="cookies.txt yok"), 404

    lines = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for l in f:
            l = l.strip()
            if l and not l.startswith("#"):
                lines.append(l)

    keys_present = set()
    for l in lines:
        parts = l.split()
        if len(parts) >= 7:
            keys_present.add(parts[5])

    required = {"SID","__Secure-3PSID","SAPISID","APISID","HSID","SSID","CONSENT"}
    return jsonify(
        ok=True,
        total_lines=len(lines),
        youtube_domain_lines=sum(1 for l in lines if "youtube.com" in l or ".youtube." in l),
        required_found=sorted(list(required & keys_present)),
        missing=sorted(list(required - keys_present)),
    )

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        url = (request.form.get("url") or "").strip()
        up = request.files.get("cookies")
        if up and up.filename:
            up.save("/tmp/cookies.txt")
            print("[cookie] uploaded -> /tmp/cookies.txt")
        try:
            filename = run_download(url)
            # Başarılı indirme: /done'a git (buton tıklanınca indirme ve otomatik / dönüş)
            return redirect(url_for("done", filename=filename))
        except Exception as e:
            msg = f"❌ İndirme Hatası: {e}"
            html = HTML_BASE.replace("{% block content %}{% endblock %}", HTML_FORM)
            return render_template_string(html, msg=msg, msg_ok=False, url=url), 400

    # GET: boş form
    html = HTML_BASE.replace("{% block content %}{% endblock %}", HTML_FORM)
    return render_template_string(html, msg=None, msg_ok=True, url="")

@app.get("/done")
def done():
    filename = request.args.get("filename")
    if not filename:
        return redirect(url_for("index"))
    html = HTML_BASE.replace("{% block content %}{% endblock %}", HTML_DONE)
    return render_template_string(html, filename=filename)

@app.route("/download/<path:filename>")
def download(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
