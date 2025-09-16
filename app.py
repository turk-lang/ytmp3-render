# -*- coding: utf-8 -*-
"""
Enhanced YouTube MP3 Downloader with Anti-Bot Protection
- Multiple bypass strategies
- Advanced client rotation
- Proxy support
- Cookie validation
"""
import os
import shutil
import re
import time
import random
from typing import Optional, Dict, Any, List, Tuple

from flask import Flask, request, send_from_directory, render_template_string, jsonify
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# ---------------- CONFIG ----------------
DOWNLOAD_DIR = os.path.abspath(os.environ.get("DOWNLOAD_DIR", "/var/data"))
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

PROXY = (
    os.environ.get("YTDLP_PROXY")
    or os.environ.get("HTTPS_PROXY")
    or os.environ.get("HTTP_PROXY")
    or os.environ.get("PROXY")
)

# Anti-bot user agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
]

# ---------------- HTML ------------------
HTML = r"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>YouTube → MP3 (Anti-Bot Korumalı)</title>
  <style>
    body{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;
         max-width:780px;margin:32px auto;padding:0 16px;line-height:1.5}
    input[type=text]{width:100%;padding:12px;border:1px solid #bbb;border-radius:10px}
    .row{display:flex;gap:8px;align-items:center;margin-top:12px}
    input[type=file]{flex:1}
    button{padding:10px 16px;border:0;border-radius:10px;background:#000;color:#fff;cursor:pointer}
    .msg{margin-top:14px;white-space:pre-wrap}
    .error{color:#d32f2f;background:#ffebee;padding:12px;border-radius:8px}
    .success{color:#388e3c;background:#e8f5e8;padding:12px;border-radius:8px}
    a.btn{display:inline-block;margin-top:8px;padding:8px 12px;background:#0a7;color:#fff;border-radius:8px;text-decoration:none}
    .note{margin-top:16px;font-size:.95em;color:#777}
    .warning{background:#fff3cd;color:#856404;padding:12px;border-radius:8px;margin:10px 0}
    code{background:#eee;padding:1px 5px;border-radius:6px}
    .status{margin:10px 0;padding:8px;background:#f8f9fa;border-left:4px solid #007bff;border-radius:4px}
  </style>
</head>
<body>
  <h2>🛡️ YouTube → MP3 (Anti-Bot Korumalı)</h2>
  
  {% if cookie_status %}
  <div class="status">
    <strong>Cookie Durumu:</strong> {{ cookie_status }}
  </div>
  {% endif %}
  
  <form method="post" enctype="multipart/form-data">
    <input type="text" name="url" placeholder="https://www.youtube.com/watch?v=..." value="{{url or ''}}" required>
    <div class="row">
      <input type="file" name="cookies" accept=".txt">
      <button type="submit">🚀 Anti-Bot İndir</button>
    </div>
  </form>
  
  {% if msg %}
    <div class="msg {{ 'error' if 'Hata' in msg else ('success' if '✅' in msg else '') }}">{{ msg|safe }}</div>
  {% endif %}
  
  {% if filename %}
    <p class="success">✅ Hazır: <a class="btn" href="/download/{{ filename }}">📥 Dosyayı İndir</a></p>
  {% endif %}
  
  <div class="warning">
    <strong>⚠️ YouTube Bot Koruması Aktif!</strong><br>
    Eğer indirme başarısız oluyorsa:
    <ol>
      <li><strong>Güncel Cookie:</strong> Chrome'dan güncel cookies.txt yükleyin</li>
      <li><strong>VPN:</strong> Farklı ülke konumu deneyin</li>
      <li><strong>Bekleme:</strong> Bir süre bekleyip tekrar deneyin</li>
    </ol>
  </div>
  
  <div class="note">
    <strong>Cookie Nasıl Alınır:</strong><br>
    1. Chrome'da YouTube'a giriş yapın<br>
    2. F12 → Application → Cookies → youtube.com<br>
    3. Tüm cookie'leri kopyalayıp .txt dosyası yapın<br>
    <br>
    <strong>Ya da:</strong> <code>yt-dlp --cookies-from-browser chrome</code> ile otomatik alın
  </div>
</body>
</html>
"""

# ---------------- ENHANCED UTILS -----------------
def ffmpeg_available() -> bool:
    """Check if FFmpeg is available"""
    return shutil.which("ffmpeg") is not None

def is_valid_youtube_url(url: str) -> bool:
    """Validate YouTube URL"""
    youtube_regex = re.compile(
        r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})'
    )
    return bool(youtube_regex.match(url))

def validate_cookies(cookiefile: str) -> Tuple[bool, str]:
    """Validate cookie file content and freshness"""
    if not os.path.exists(cookiefile):
        return False, "Cookie dosyası bulunamadı"
    
    try:
        with open(cookiefile, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check if it's a valid Netscape format
        if not content.strip().startswith('# Netscape HTTP Cookie File'):
            return False, "Geçersiz cookie formatı"
        
        # Count valid cookies
        lines = content.split('\n')
        valid_cookies = 0
        for line in lines:
            if line.strip() and not line.startswith('#'):
                parts = line.split('\t')
                if len(parts) >= 7 and 'youtube.com' in parts[0]:
                    valid_cookies += 1
        
        if valid_cookies == 0:
            return False, "YouTube cookie'leri bulunamadı"
        
        # Check file age
        age_hours = (time.time() - os.path.getmtime(cookiefile)) / 3600
        if age_hours > 24:
            return True, f"⚠️ Cookie eski ({int(age_hours)} saat) - yenilemek önerilir"
        
        return True, f"✅ Geçerli ({valid_cookies} cookie, {int(age_hours)}h eski)"
        
    except Exception as e:
        return False, f"Cookie okuma hatası: {e}"

def ensure_cookiefile() -> Tuple[Optional[str], str]:
    """Find and validate cookie file"""
    tmp = "/tmp/cookies.txt"
    status = "Cookie bulunamadı"
    
    if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        is_valid, msg = validate_cookies(tmp)
        if is_valid:
            return tmp, msg
        status = f"Geçersiz cookie: {msg}"
    
    candidates = [
        os.environ.get("YTDLP_COOKIES"),
        "/etc/secrets/cookies.txt",
        "/etc/secrets/COOKIES.txt",
        "/etc/secrets/youtube-cookies.txt",
        "/app/cookies.txt",
    ]
    
    for src in candidates:
        if src and os.path.exists(src) and os.path.getsize(src) > 0:
            try:
                shutil.copyfile(src, tmp)
                is_valid, msg = validate_cookies(tmp)
                if is_valid:
                    return tmp, msg
                status = f"Geçersiz cookie: {msg}"
            except Exception as e:
                print(f"Cookie kopyalama hatası: {e}")
    
    return None, status

def get_random_user_agent() -> str:
    """Get random user agent for anti-bot"""
    return random.choice(USER_AGENTS)

def common_opts(client_order: List[str], cookiefile: Optional[str], bypass_mode: bool = False) -> Dict[str, Any]:
    """Enhanced yt-dlp options with anti-bot measures"""
    user_agent = get_random_user_agent()
    
    opts: Dict[str, Any] = {
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title).90s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,  # Enable warnings for debugging
        "cachedir": False,
        "retries": 5,
        "fragment_retries": 10,
        "nocheckcertificate": True,
        
        # Anti-bot headers
        "http_headers": {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        },
        
        # Enhanced extractor args
        "extractor_args": {
            "youtube": {
                "player_client": client_order,
                "skip": ["configs", "webpage"] if bypass_mode else ["configs"],
                "player_skip": ["webpage"] if bypass_mode else [],
            }
        },
        
        # Geo bypass
        "geo_bypass": True,
        "geo_bypass_country": ["US", "GB", "DE", "CA"],
        
        # Sleep between requests
        "sleep_interval": random.uniform(1, 3),
        "max_sleep_interval": 5,
        
        # Additional anti-bot measures
        "extractor_retries": 3,
        "file_access_retries": 3,
    }
    
    # Add proxy if available
    if PROXY:
        opts["proxy"] = PROXY
    
    # Add cookies if available
    if cookiefile and os.path.exists(cookiefile):
        opts["cookiefile"] = cookiefile
    
    # FFmpeg postprocessor
    if ffmpeg_available():
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    
    return opts

def choose_format(info: Dict[str, Any]) -> str:
    """Enhanced format selection"""
    fmts = info.get("formats") or []
    candidates: List[Tuple[float, Dict[str, Any]]] = []
    
    for f in fmts:
        acodec, vcodec = f.get("acodec"), f.get("vcodec")
        if not acodec or acodec == "none":
            continue
            
        is_audio_only = (vcodec in (None, "none"))
        abr = f.get("abr") or f.get("tbr") or 0
        ext = (f.get("ext") or "").lower()
        
        # Prefer higher quality audio-only formats
        ext_bonus = {
            "m4a": 30,
            "aac": 25, 
            "webm": 20,
            "opus": 15,
            "mp3": 10,
        }.get(ext, 0)
        
        score = abr + (100 if is_audio_only else 0) + ext_bonus
        candidates.append((score, f))
    
    if not candidates:
        return "bestaudio/best"
    
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1].get("format_id") or "bestaudio/best"

def run_download(url: str) -> str:
    """Enhanced download with multiple anti-bot strategies"""
    if not url.strip():
        raise ValueError("URL boş olamaz.")
    
    if not is_valid_youtube_url(url):
        raise ValueError("Geçerli bir YouTube URL'si giriniz.")
    
    cookie, cookie_status = ensure_cookiefile()
    print(f"Cookie durumu: {cookie_status}")
    
    # Multiple strategy attempts
    strategies = [
        # Strategy 1: Cookie-based with web client
        {
            "clients": ["web", "android", "tv"],
            "bypass": False,
            "name": "Cookie + Web Client"
        },
        # Strategy 2: Mobile clients without cookies
        {
            "clients": ["android", "ios", "tv"],
            "bypass": False,
            "name": "Mobile Clients",
            "force_no_cookie": True
        },
        # Strategy 3: TV client with bypass mode
        {
            "clients": ["tv", "android"],
            "bypass": True,
            "name": "TV Client Bypass"
        },
        # Strategy 4: iOS client only
        {
            "clients": ["ios"],
            "bypass": True,
            "name": "iOS Client Only"
        },
        # Strategy 5: All clients with maximum bypass
        {
            "clients": ["mweb", "tv", "android", "ios"],
            "bypass": True,
            "name": "All Clients Bypass"
        }
    ]
    
    last_err = None
    
    for i, strategy in enumerate(strategies, 1):
        try:
            print(f"Strateji {i}/{len(strategies)}: {strategy['name']}")
            
            # Use cookie unless explicitly disabled
            use_cookie = cookie if not strategy.get("force_no_cookie") else None
            
            # Add random delay between attempts
            if i > 1:
                time.sleep(random.uniform(2, 5))
            
            # Extract info first
            opts_probe = common_opts(
                strategy["clients"], 
                use_cookie, 
                strategy["bypass"]
            )
            
            with YoutubeDL(opts_probe) as y1:
                info = y1.extract_info(url, download=False)
                
                if not info:
                    raise DownloadError("Video bilgisi alınamadı")
                
                if info.get("is_live"):
                    raise DownloadError("Canlı yayın desteklenmiyor")
                
                # Choose format
                fmt = choose_format(info)
                print(f"Seçilen format: {fmt}")
            
            # Download with chosen format
            opts_dl = dict(opts_probe)
            opts_dl["format"] = fmt
            
            # Track files
            before = set(os.listdir(DOWNLOAD_DIR)) if os.path.exists(DOWNLOAD_DIR) else set()
            
            with YoutubeDL(opts_dl) as y2:
                y2.download([url])
            
            # Find new files
            after = set(os.listdir(DOWNLOAD_DIR)) if os.path.exists(DOWNLOAD_DIR) else set()
            new_files = sorted(
                after - before,
                key=lambda f: os.path.getmtime(os.path.join(DOWNLOAD_DIR, f)),
                reverse=True
            )
            
            if new_files:
                print(f"✅ Başarılı: {strategy['name']}")
                return new_files[0]
            
            raise DownloadError("Dosya oluşturulamadı")
            
        except Exception as e:
            last_err = e
            print(f"❌ Strateji {i} başarısız: {e}")
            continue
    
    # All strategies failed
    error_msg = str(last_err) if last_err else "Bilinmeyen hata"
    if "Sign in to confirm" in error_msg:
        raise RuntimeError(
            "🤖 YouTube bot koruması aktif!\n\n"
            "Çözümler:\n"
            "1. Güncel cookie dosyası yükleyin\n"
            "2. VPN kullanarak farklı ülkeden deneyin\n"
            "3. Birkaç dakika bekleyip tekrar deneyin\n"
            "4. YouTube'a giriş yapıp cookie'leri yenileyin"
        )
    
    raise RuntimeError(f"Tüm anti-bot stratejileri başarısız: {error_msg}")

# ---------------- FLASK APP -----------------
app = Flask(__name__)

@app.get("/health")
def health():
    """Enhanced health check"""
    cookie, cookie_status = ensure_cookiefile()
    return jsonify({
        "ok": True,
        "ffmpeg": ffmpeg_available(),
        "download_dir": DOWNLOAD_DIR,
        "proxy": bool(PROXY),
        "cookie_status": cookie_status,
        "user_agents": len(USER_AGENTS)
    })

@app.route("/", methods=["GET", "POST"])
def index():
    """Main route with enhanced error handling"""
    msg, filename, url = None, None, ""
    cookie, cookie_status = ensure_cookiefile()
    
    if request.method == "POST":
        url = (request.form.get("url") or "").strip()
        file = request.files.get("cookies")
        
        # Handle uploaded cookie file
        if file and file.filename and file.filename.endswith('.txt'):
            try:
                file.save("/tmp/cookies.txt")
                cookie, cookie_status = ensure_cookiefile()  # Re-validate
                print("Cookie dosyası yüklendi ve doğrulandı")
            except Exception as e:
                print(f"Cookie dosyası yükleme hatası: {e}")
        
        # Attempt download
        if url:
            try:
                filename = run_download(url)
                msg = "✅ İndirme tamamlandı! Anti-bot koruması aşıldı."
                print(f"İndirme başarılı: {filename}")
            except Exception as e:
                msg = f"❌ İndirme Hatası:\n{e}"
                print(f"İndirme hatası: {e}")
        else:
            msg = "❌ URL giriniz."
    
    return render_template_string(
        HTML, 
        msg=msg, 
        filename=filename, 
        url=url,
        cookie_status=cookie_status
    )

@app.route("/download/<path:filename>")
def download(filename):
    """Serve downloaded files"""
    try:
        return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)
    except FileNotFoundError:
        return "Dosya bulunamadı", 404
    except Exception as e:
        return f"Dosya indirme hatası: {e}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print("🛡️ Enhanced Anti-Bot YouTube MP3 Downloader Starting...")
    print(f"📁 Download directory: {DOWNLOAD_DIR}")
    print(f"🎵 FFmpeg available: {ffmpeg_available()}")
    print(f"🌐 Proxy configured: {bool(PROXY)}")
    print(f"🔧 User agents: {len(USER_AGENTS)}")
    
    cookie, status = ensure_cookiefile()
    print(f"🍪 Cookie status: {status}")
    
    app.run(host="0.0.0.0", port=port, debug=False)