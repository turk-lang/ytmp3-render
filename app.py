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

def build_opts(*, player_clients, cookiefile: Optional[str] = None, proxy: Optional[str] = PROXY, postprocess: bool = True, use_po_token: bool = False, aggressive_bypass: bool = False) -> Dict[str, Any]:
    """player_clients: list[str] veya str kabul eder → stringe çevrilir."""
    if isinstance(player_clients, list):
        player_clients = ",".join(player_clients)  # ✅ list → string
    assert isinstance(player_clients, str), "player_clients string olmalı"

    # More realistic User-Agents (latest versions)
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    ]
    import random
    selected_ua = random.choice(user_agents)

    opts: Dict[str, Any] = {
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title).90s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "cachedir": False,
        "retries": 6 if aggressive_bypass else 4,
        "fragment_retries": 6 if aggressive_bypass else 4,
        "concurrent_fragment_downloads": 1 if aggressive_bypass else 2,
        "nocheckcertificate": True,
        "socket_timeout": 60 if aggressive_bypass else 45,
        "http_chunk_size": 262144 if aggressive_bypass else 524288,  # Even smaller chunks for aggressive mode
        "source_address": "0.0.0.0",
        "sleep_interval_requests": 2 if aggressive_bypass else 1,
        "max_sleep_interval": 8 if aggressive_bypass else 3,
        "http_headers": {
            "User-Agent": selected_ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        },
        "extractor_args": {
            "youtube": {
                "player_client": player_clients,
                "skip": ["configs"] if not aggressive_bypass else ["configs", "webpage", "js"],
                "player_skip": ["js"] if not aggressive_bypass else ["js", "configs"],
                "comment_sort": "top",
                "max_comments": [0, 0, 0],  # Disable comment fetching
            }
        },
        "geo_bypass_country": "US",
        "no_check_formats": True,
        "ignore_no_formats_error": True,
        "ignoreerrors": False,  # Don't ignore critical errors
    }
    
    # Add PO Token if available and requested
    if use_po_token:
        po_token = os.environ.get("YTDLP_PO_TOKEN")
        visitor_data = os.environ.get("YTDLP_VISITOR_DATA")
        if po_token and visitor_data:
            opts["extractor_args"]["youtube"]["po_token"] = f"{po_token}:{visitor_data}"
            print(f"[token] Using PO Token bypass")
    
    if proxy:
        opts["proxy"] = proxy
        print(f"[proxy] Using proxy: {proxy[:20]}...")
    
    if cookiefile:
        opts["cookiefile"] = cookiefile
    
    if postprocess and ffmpeg_available():
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
            "nopostoverwrites": False,
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

    # Enhanced strategies with more sophisticated bypass techniques
    strategies = [
        # Format: (name, clients, use_po_token, aggressive_bypass, delay)
        ("PO Token + TV", ["tv"], True, False, 1),
        ("PO Token + Android", ["android"], True, False, 1),
        ("Cookie + iOS", ["ios"], False, False, 2),
        ("Cookie + TV Embedded", ["tv_embedded"], False, True, 2),
        ("Android Mobile", ["android"], False, False, 2),
        ("TV Client", ["tv"], False, True, 3),
        ("Web + Android Mix", ["web", "android"], False, True, 3),
        ("iOS Fallback", ["ios"], False, True, 4),
        ("All Clients Emergency", ["android", "tv", "ios", "web"], False, True, 5),
    ]

    last_err = None
    for idx, (name, clients, use_po_token, aggressive_bypass, base_delay) in enumerate(strategies, start=1):
        print(f"Strateji {idx}/{len(strategies)}: {name} -> {','.join(clients)}")
        
        # Progressive delay with variation
        if idx > 1:
            delay = base_delay + (idx * 0.5)
            print(f"  🕒 {delay:.1f}s bekleniyor...")
            time.sleep(delay)
        
        try:
            # 1) Probe with enhanced options
            opts_probe = build_opts(
                player_clients=clients, 
                cookiefile=cookie, 
                postprocess=False, 
                use_po_token=use_po_token,
                aggressive_bypass=aggressive_bypass
            )
            
            with YoutubeDL(opts_probe) as y1:
                print(f"  📡 Video bilgileri alınıyor...")
                info = y1.extract_info(url, download=False)
                
                if not info:
                    raise DownloadError("Video bilgileri alınamadı")
                    
                if info.get("is_live"):
                    raise DownloadError("Canlı yayın desteklenmiyor.")
                    
                # Check if video is available
                if info.get("availability") in ["private", "premium_only", "subscriber_only", "needs_auth"]:
                    raise DownloadError(f"Video erişilemez durumda: {info.get('availability')}")
                
                fmt = choose_format(info)
                print(f"  🎵 Format seçildi: {fmt}")

            # Delay between probe and download - critical for avoiding detection
            delay_between = 1.5 if aggressive_bypass else 0.8
            time.sleep(delay_between)

            # 2) Download with same enhanced options
            opts_dl = build_opts(
                player_clients=clients, 
                cookiefile=cookie, 
                postprocess=True, 
                use_po_token=use_po_token,
                aggressive_bypass=aggressive_bypass
            )
            opts_dl["format"] = fmt

            print(f"  ⬇️ İndirme başlıyor...")
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
                print(f"  ✅ Başarılı: {new_files[0]}")
                return new_files[0]

            # Fallback filename generation
            title = (info.get("title") or "audio").strip()
            ext = "mp3" if ffmpeg_available() else (info.get("ext") or "m4a")
            safe = "".join(c for c in f"{title}.{ext}" if c not in "\\/:*?\"<>|").strip()
            if safe and len(safe) > 4:  # Valid filename
                return safe

        except Exception as e:
            last_err = e
            error_msg = str(e).lower()
            print(f"❌ Strateji {idx} başarısız: {e}")
            
            # Specific error handling
            if "failed to extract any player response" in error_msg:
                print("  🔍 Player response hatası - daha agresif bypass gerekiyor")
                if not cookie_refreshed and idx <= 3:
                    print("  🔄 Cookie refresh + uzun bekleme...")
                    cookie = ensure_cookiefile(refresh=True)
                    cookie_refreshed = True
                    time.sleep(5)  # Longer wait after player response failure
                    
            elif "sign in to confirm" in error_msg or "bot" in error_msg:
                print("  🤖 Bot detection - cookie + proxy önerilir")
                if not cookie_refreshed and idx <= 2:
                    cookie = ensure_cookiefile(refresh=True)
                    cookie_refreshed = True
                    time.sleep(4)
                    
            elif "private" in error_msg or "unavailable" in error_msg:
                print("  🚫 Video erişilemez - diğer stratejiler de başarısız olacak")
                break  # No point trying other strategies
                
            elif "rate" in error_msg or "limit" in error_msg:
                print("  ⏳ Rate limit - uzun bekleme")
                time.sleep(8)
                
            elif "network" in error_msg or "timeout" in error_msg:
                print("  🌐 Ağ hatası - kısa bekleme")
                time.sleep(2)
            
            continue

    # All strategies failed
    msg = str(last_err) if last_err else "Bilinmeyen hata"
    low = msg.lower()
    
    # Enhanced error messages with specific solutions
    hint = ""
    if "failed to extract any player response" in low:
        hint = ("\n\n🔧 Player Response Hatası - Çözüm Önerileri:"
                "\n• Cookies.txt dosyasını Chrome'dan yeniden export edin"
                "\n• YTDLP_PROXY ile residential/sticky proxy kullanın"
                "\n• YTDLP_PO_TOKEN ve YTDLP_VISITOR_DATA environment variables ekleyin"
                "\n• 15-30 dakika bekleyip tekrar deneyin"
                "\n• Farklı bir network/IP'den deneyin")
    elif ("sign in to confirm you're not a bot" in low) or ("bot olmadığınızı" in low):
        hint = ("\n\n🤖 Bot Detection - Çözüm Önerileri:"
                "\n• Fresh cookies.txt dosyası yükleyin (oturum açık Chrome'dan)"
                "\n• Kaliteli residential proxy kullanın"
                "\n• VPN değiştirip farklı lokasyondan deneyin"
                "\n• 10-15 dakika bekleyip tekrar deneyin")
    elif ("private" in low) or ("unavailable" in low):
        hint = "\n\n❌ Video özel, kaldırılmış veya coğrafi olarak engellenimiş."
    elif ("rate" in low) or ("limit" in low):
        hint = "\n\n⏳ Rate limit aşıldı. 15-30 dakika bekleyip tekrar deneyin."
    else:
        hint = ("\n\n💡 Genel Çözüm Önerileri:"
                "\n• Video URL'sinin doğru ve erişilebilir olduğundan emin olun"
                "\n• Cookies.txt ve proxy ayarlarını kontrol edin"
                "\n• Yt-dlp'nin güncel olduğundan emin olun")
    
    raise RuntimeError(f"Tüm bypass stratejileri başarısız: {msg}{hint}")

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