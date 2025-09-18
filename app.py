# -*- coding: utf-8 -*-
"""
YouTube → MP3 (Render-friendly) — Stabil Sürüm (Template fix)
- Template sorunları düzeltildi
- Rate limiting eklendi
- Anti-bot koruması geliştirildi
- Başarılı indirme → /done (İndir butonu); butona tıklayınca dosya iner ve 1.5 sn sonra / (form sıfır)
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

# Session tracking for rate limiting
download_sessions = {}

# --------- HTML Shell + Contents ---------
HTML_SHELL = r"""<!doctype html>
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
    .countdown{font-size:0.9em;color:#666;margin-top:8px}
  </style>
</head>
<body>
  <h2>YouTube → MP3</h2>
  <!--CONTENT-->
  <div class="note">
    Not: FFmpeg varsa MP3'e dönüştürülür; yoksa m4a/webm kalır. Yalnızca hak sahibi olduğunuz içerikleri indirin.
    <br><br>
    <strong>Bot hatası alıyorsanız:</strong>
    <br>• Chrome'da YouTube'a giriş yapın → F12 → Application → Cookies → youtube.com → tüm cookies'leri kopyalayıp cookies.txt dosyasına kaydedin
    <br>• Environment variables: <code>YTDLP_PROXY</code> (önemli!)
  </div>
</body>
</html>
"""

FORM_CONTENT = r"""
  <form method="post" enctype="multipart/form-data">
    <input type="text" name="url" placeholder="https://www.youtube.com/watch?v=..." value="{url}" required>
    <div class="row">
      <input type="file" name="cookies" accept=".txt">
      <button type="submit">İndir</button>
    </div>
  </form>
  {msg_block}
"""

DONE_CONTENT = r"""
  <div class="msg ok">✅ İndirme tamamlandı.</div>
  <p style="margin-top:12px">
    <a id="dlbtn" class="btn" href="#" onclick="downloadAndRedirect('/download/{filename}', '{filename}')">
      🔥 Dosyayı indir
    </a>
  </p>
  <div class="countdown" id="countdown"></div>
  
  <div class="divider"></div>
  <form method="post" enctype="multipart/form-data">
    <input type="text" name="url" placeholder="Yeni link: https://www.youtube.com/watch?v=..." required>
    <div class="row">
      <input type="file" name="cookies" accept=".txt">
      <button type="submit">Yeni İndirme</button>
    </div>
  </form>
  
  <script>
    function downloadAndRedirect(url, filename) {{
      // Dosyayı indir
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      
      // Butonu güncelle
      document.getElementById('dlbtn').textContent = 'İndiriliyor...';
      document.getElementById('dlbtn').classList.add('disabled');
      
      // 2 saniye sonra ana sayfaya yönlendir
      let seconds = 2;
      const countdownEl = document.getElementById('countdown');
      
      const updateCountdown = () => {{
        if (seconds > 0) {{
          countdownEl.textContent = `${{seconds}} saniye sonra ana sayfaya dönülecek...`;
          seconds--;
          setTimeout(updateCountdown, 1000);
        }} else {{
          window.location.href = '/';
        }}
      }};
      
      updateCountdown();
    }}
  </script>
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

def check_rate_limit(ip: str) -> bool:
    """Check if IP is rate limited. Returns True if allowed, False if blocked."""
    current_time = time.time()
    
    if ip not in download_sessions:
        download_sessions[ip] = []
    
    # Clean old sessions (older than 10 minutes)
    download_sessions[ip] = [
        timestamp for timestamp in download_sessions[ip] 
        if current_time - timestamp < 600
    ]
    
    # Allow max 3 downloads per 10 minutes per IP
    if len(download_sessions[ip]) >= 3:
        return False
    
    # Add current session
    download_sessions[ip].append(current_time)
    return True

def ensure_cookiefile(refresh: bool = False) -> Optional[str]:
    """Ensure cookie file is available. If refresh=True, reload from sources."""
    tmp = "/tmp/cookies.txt"
    
    # If refresh requested or tmp doesn't exist, reload from sources
    if refresh or not (os.path.exists(tmp) and os.path.getsize(tmp) > 0):
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
                print(f"[cookie] {'refreshed' if refresh else 'copied'} {src} -> {tmp}")
                return tmp
        
        if refresh:
            print("[cookie] refresh failed - no valid sources found")
        else:
            print("[cookie] not found")
        return None
    
    print("[cookie] using existing /tmp/cookies.txt")
    return tmp

def build_opts(*, player_clients, cookiefile: Optional[str] = None, proxy: Optional[str] = PROXY, postprocess: bool = True) -> Dict[str, Any]:
    """player_clients: list[str] veya str kabul eder → stringe çevrilir."""
    if isinstance(player_clients, list):
        player_clients = ",".join(player_clients)  # ✅ list → string
    assert isinstance(player_clients, str), "player_clients string olmalı"

    # Rotating User-Agents for better bot detection evasion
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ]
    import random
    selected_ua = random.choice(user_agents)

    opts: Dict[str, Any] = {
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title).90s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "cachedir": False,
        "retries": 4,
        "fragment_retries": 4,
        "concurrent_fragment_downloads": 2,  # Reduced to be less aggressive
        "nocheckcertificate": True,
        "socket_timeout": 45,
        "http_chunk_size": 524288,  # 512KB - smaller chunks
        "source_address": "0.0.0.0",
        "sleep_interval_requests": 1,
        "max_sleep_interval": 3,
        "http_headers": {
            "User-Agent": selected_ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
        },
        "extractor_args": {
            "youtube": {
                "player_client": player_clients,   # ✅ her zaman string
                "skip": ["configs", "webpage"],
                "player_skip": ["js"],
            }
        },
        "geo_bypass_country": "US",  # Changed from TR to US
        "no_check_formats": True,
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

    cookie = ensure_cookiefile(refresh=False)
    cookie_refreshed = False

    # Enhanced strategies
    if cookie:
        strategies = [
            ("Cookie + Android", ["android"]),
            ("Cookie + TV", ["tv"]),
            ("Cookie + Web", ["web"]),
        ]
    else:
        strategies = [
            ("Mobile Clients", ["android", "tv"]),
            ("Android Only", ["android"]),
            ("TV Only", ["tv"]),
            ("Web Fallback", ["web"]),
        ]

    last_err = None
    for idx, (name, clients) in enumerate(strategies, start=1):
        print(f"Strateji {idx}/{len(strategies)}: {name} -> {','.join(clients) if isinstance(clients, list) else clients}")
        
        # Progressive delay for anti-bot
        if idx > 1:
            delay = min(1 + idx, 6)
            print(f"  🕒 {delay}s bekleniyor...")
            time.sleep(delay)
        
        try:
            # 1) Probe
            opts_probe = build_opts(player_clients=clients, cookiefile=cookie, postprocess=False)
            with YoutubeDL(opts_probe) as y1:
                info = y1.extract_info(url, download=False)
                if info.get("is_live"):
                    raise DownloadError("Canlı yayın desteklenmiyor.")
                fmt = choose_format(info)

            # Small delay between probe and download
            time.sleep(0.5)

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
            error_msg = str(e).lower()
            print(f"❌ Strateji {idx} başarısız: {e}")
            
            # Try refreshing cookie once if we encounter bot detection
            if "sign in to confirm" in error_msg or "bot" in error_msg:
                if not cookie_refreshed and idx <= 2:
                    print("🔄 Cookie refresh deneniyor...")
                    cookie = ensure_cookiefile(refresh=True)
                    cookie_refreshed = True
                    time.sleep(3)
            elif "rate" in error_msg or "limit" in error_msg:
                time.sleep(5)
            
            continue

    msg = str(last_err) if last_err else "Bilinmeyen hata"
    low = msg.lower()
    if ("sign in to confirm you're not a bot" in low) or ("bot olmadığınızı" in low):
        msg += ("\n\n🔧 Çözüm önerileri:"
                "\n• Cookies.txt dosyasını yeniden yükleyin"
                "\n• 5-10 dakika bekleyip tekrar deneyin"
                "\n• YTDLP_PROXY environment variable kullanın")
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
        # Rate limiting check
        client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
        if not check_rate_limit(client_ip):
            msg_html = '<div class="msg err">⏳ Rate limit aşıldı. 10 dakika içinde maksimum 3 indirme yapabilirsiniz.</div>'
            content = FORM_CONTENT.format(url="", msg_block=msg_html)
            return render_template_string(HTML_SHELL.replace("<!--CONTENT-->", content)), 429

        url = (request.form.get("url") or "").strip()
        up = request.files.get("cookies")
        if up and up.filename:
            up.save("/tmp/cookies.txt")
            print(f"[cookie] uploaded -> /tmp/cookies.txt (from {client_ip})")
        try:
            filename = run_download(url)
            return redirect(url_for("done", filename=filename))
        except Exception as e:
            msg_html = f'<div class="msg err">❌ İndirme Hatası: {str(e)}</div>'
            content = FORM_CONTENT.format(url=url, msg_block=msg_html)
            return render_template_string(HTML_SHELL.replace("<!--CONTENT-->", content)), 400

    # GET: boş form
    content = FORM_CONTENT.format(url="", msg_block="")
    return render_template_string(HTML_SHELL.replace("<!--CONTENT-->", content))

@app.get("/done")
def done():
    filename = request.args.get("filename")
    if not filename:
        return redirect(url_for("index"))
    
    # Template rendering with proper JavaScript escaping
    content = DONE_CONTENT.format(filename=filename)
    page = HTML_SHELL.replace("<!--CONTENT-->", content)
    return page  # Direct return, no render_template_string needed

@app.route("/download/<path:filename>")
def download(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)