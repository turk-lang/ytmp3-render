# -*- coding: utf-8 -*-
"""
YouTube → MP3 (Render-friendly) — Enhanced Anti-Bot Version
- Fixed player response extraction issues
- Enhanced bot protection bypass
- Improved error handling and recovery
- Better cookie management
- Rate limiting with IP tracking
"""

import os
import re
import time
import shutil
import random
import json
import threading
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse, parse_qs

from flask import Flask, request, send_from_directory, render_template_string, jsonify, redirect, url_for
try:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError, ExtractorError
except ImportError as e:
    print(f"HATA: yt-dlp kurulu değil! pip install yt-dlp çalıştırın: {e}")
    exit(1)

# --------- Config ----------
DOWNLOAD_DIR = os.path.abspath(os.environ.get("DOWNLOAD_DIR", "/var/data"))
try:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
except PermissionError:
    print(f"HATA: {DOWNLOAD_DIR} dizini oluşturulamıyor. İzin hatası!")
    DOWNLOAD_DIR = "/tmp/downloads"
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    print(f"Alternatif dizin kullanılıyor: {DOWNLOAD_DIR}")

PROXY = (
    os.environ.get("YTDLP_PROXY")
    or os.environ.get("HTTPS_PROXY")
    or os.environ.get("HTTP_PROXY")
    or os.environ.get("PROXY")
)

# Enhanced session tracking
download_sessions = {}
failed_urls = {}  # Track failed URLs to avoid repeated attempts
session_lock = threading.Lock()

# User agents pool - latest versions
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
]

# --------- HTML Templates ---------
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
    input[type=text]{width:100%;padding:12px;border:1px solid #cbd5e1;border-radius:10px;box-sizing:border-box}
    .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px}
    input[type=file]{flex:1}
    button,a.btn{padding:10px 16px;border:0;border-radius:10px;background:var(--primary);color:#fff;cursor:pointer;text-decoration:none;display:inline-block;box-sizing:border-box}
    button[disabled],a.btn.disabled{opacity:.6;pointer-events:none}
    .msg{margin-top:14px;white-space:pre-wrap;max-height:300px;overflow-y:auto}
    .ok{background:var(--okbg);color:#14532d;padding:12px;border-radius:8px}
    .err{background:var(--errbg);color:var(--err);padding:12px;border-radius:8px}
    .note{margin-top:16px;font-size:.95em;color:var(--muted)}
    .divider{height:1px;background:#e5e7eb;margin:20px 0}
    .countdown{font-size:0.9em;color:#666;margin-top:8px}
    .progress{margin-top:12px;background:#f3f4f6;border-radius:4px;overflow:hidden}
    .progress-bar{height:6px;background:var(--ok);width:0;transition:width 0.3s ease}
    .details{font-size:0.85em;margin-top:8px;color:var(--muted)}
  </style>
</head>
<body>
  <h2>YouTube → MP3</h2>
  <!--CONTENT-->
  <div class="note">
    <strong>Not:</strong> FFmpeg varsa MP3'e dönüştürülür; yoksa m4a/webm kalır. Yalnızca hak sahibi olduğunuz içerikleri indirin.
    <br><br>
    <strong>Bot hatası alıyorsanız:</strong>
    <br>• Chrome'da YouTube'a giriş yapın → F12 → Application → Cookies → youtube.com → tüm cookies'leri kopyalayıp cookies.txt dosyasına kaydedin
    <br>• Environment variables: <code>YTDLP_PROXY</code> (önemli!)
    <br>• PO Token ve Visitor Data ekleyin (gelişmiş kullanıcılar için)
  </div>
</body>
</html>
"""

FORM_CONTENT = r"""
  <form method="post" enctype="multipart/form-data" id="downloadForm">
    <input type="text" name="url" placeholder="https://www.youtube.com/watch?v=..." value="{url}" required>
    <div class="row">
      <input type="file" name="cookies" accept=".txt">
      <button type="submit" id="submitBtn">İndir</button>
    </div>
  </form>
  {msg_block}
  
  <script>
    document.getElementById('downloadForm').addEventListener('submit', function(e) {{
      const btn = document.getElementById('submitBtn');
      btn.disabled = true;
      btn.textContent = 'İndiriliyor...';
      
      setTimeout(() => {{
        btn.disabled = false;
        btn.textContent = 'İndir';
      }}, 45000);
    }});
  </script>
"""

DONE_CONTENT = r"""
  <div class="msg ok">✅ İndirme tamamlandı.</div>
  <p style="margin-top:12px">
    <a id="dlbtn" class="btn" href="#" onclick="downloadAndRedirect('/download/{filename}', '{filename}')">
      📥 Dosyayı indir
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
      try {{
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        
        const dlBtn = document.getElementById('dlbtn');
        if (dlBtn) {{
          dlBtn.textContent = 'İndiriliyor...';
          dlBtn.classList.add('disabled');
        }}
        
        let seconds = 3;
        const countdownEl = document.getElementById('countdown');
        
        const updateCountdown = () => {{
          if (seconds > 0 && countdownEl) {{
            countdownEl.textContent = `${{seconds}} saniye sonra ana sayfaya dönülecek...`;
            seconds--;
            setTimeout(updateCountdown, 1000);
          }} else {{
            window.location.href = '/';
          }}
        }};
        
        updateCountdown();
      }} catch (error) {{
        console.error('Download error:', error);
        alert('İndirme sırasında hata oluştu. Lütfen tekrar deneyin.');
      }}
    }}
  </script>
"""

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# --------- Enhanced Helpers ---------
def ffmpeg_available() -> bool:
    """Check if FFmpeg is available"""
    return shutil.which("ffmpeg") is not None

def extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from various URL formats"""
    if not url:
        return None
    
    # Add https if missing
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'youtu\.be\/([0-9A-Za-z_-]{11})',
        r'shorts\/([0-9A-Za-z_-]{11})',
        r'embed\/([0-9A-Za-z_-]{11})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None

def is_valid_youtube_url(url: str) -> bool:
    """Enhanced YouTube URL validation"""
    return extract_video_id(url) is not None

def check_rate_limit(ip: str) -> Tuple[bool, int]:
    """Enhanced rate limiting with better tracking"""
    if not ip:
        return True, 0
        
    current_time = time.time()
    
    with session_lock:
        if ip not in download_sessions:
            download_sessions[ip] = []
        
        # Clean old sessions (older than 15 minutes)
        download_sessions[ip] = [
            timestamp for timestamp in download_sessions[ip] 
            if current_time - timestamp < 900
        ]
        
        # Allow max 5 downloads per 15 minutes per IP
        attempts = len(download_sessions[ip])
        if attempts >= 5:
            return False, attempts
        
        # Add current session
        download_sessions[ip].append(current_time)
        return True, attempts + 1

def get_fresh_cookies() -> Optional[str]:
    """Get fresh cookie file with better error handling"""
    tmp_path = "/tmp/cookies.txt"
    
    # Priority order for cookie sources
    candidates = [
        os.environ.get("YTDLP_COOKIES"),
        "/etc/secrets/cookies.txt",
        "/etc/secrets/COOKIES.txt", 
        "/etc/secrets/youtube-cookies.txt",
        "/app/cookies.txt",
        "./cookies.txt",
    ]
    
    for src in candidates:
        if src and os.path.exists(src) and os.path.getsize(src) > 100:  # At least 100 bytes
            try:
                shutil.copyfile(src, tmp_path)
                print(f"[cookie] Used {src} -> {tmp_path}")
                return tmp_path
            except Exception as e:
                print(f"[cookie] Failed to copy {src}: {e}")
                continue
    
    # Check if existing tmp file is still valid
    if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 100:
        print("[cookie] Using existing /tmp/cookies.txt")
        return tmp_path
    
    print("[cookie] No valid cookies found")
    return None

def build_ytdl_opts(client_name: str, cookiefile: Optional[str] = None, 
                   use_po_token: bool = False, aggressive: bool = False) -> Dict[str, Any]:
    """Build yt-dlp options with enhanced anti-bot measures"""
    
    user_agent = random.choice(USER_AGENTS)
    
    # Base options
    opts = {
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title).90s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,  # Keep warnings for debugging
        "cachedir": False,
        "nocheckcertificate": True,
        "socket_timeout": 120 if aggressive else 60,
        "retries": 8 if aggressive else 5,
        "fragment_retries": 8 if aggressive else 5,
        "concurrent_fragment_downloads": 1,  # Conservative
        "http_chunk_size": 1048576,  # 1MB chunks
        "sleep_interval_requests": 3 if aggressive else 1,
        "max_sleep_interval": 10 if aggressive else 5,
        "ignoreerrors": False,
        "no_check_formats": True,
        "source_address": "0.0.0.0",
    }
    
    # Enhanced headers to mimic real browser
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
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
        "Cache-Control": "max-age=0",
    }
    
    # Add referer for better authenticity
    if "youtube.com" in str(cookiefile or ""):
        headers["Referer"] = "https://www.youtube.com/"
    
    opts["http_headers"] = headers
    
    # Client-specific extractor args
    extractor_args = {
        "youtube": {
            "player_client": [client_name],
            "skip": ["dash"] if aggressive else [],
            "player_skip": ["js"] if aggressive else [],
            "comment_sort": "top",
            "max_comments": [0, 0, 0],
        }
    }
    
    # Add PO Token if available
    if use_po_token:
        po_token = os.environ.get("YTDLP_PO_TOKEN")
        visitor_data = os.environ.get("YTDLP_VISITOR_DATA") 
        if po_token and visitor_data:
            extractor_args["youtube"]["po_token"] = f"{po_token}:{visitor_data}"
            print(f"[token] Using PO Token authentication")
    
    opts["extractor_args"] = extractor_args
    
    # Add proxy if available
    if PROXY:
        opts["proxy"] = PROXY
        print(f"[proxy] Using: {PROXY[:30]}...")
    
    # Add cookies if available
    if cookiefile and os.path.exists(cookiefile):
        opts["cookiefile"] = cookiefile
        print(f"[cookie] Using: {cookiefile}")
    
    # Add post-processing for MP3 conversion
    if ffmpeg_available():
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
            "nopostoverwrites": False,
        }]
        print("[ffmpeg] MP3 conversion enabled")
    
    # Geo bypass
    opts["geo_bypass"] = True
    opts["geo_bypass_country"] = "US"
    
    return opts

def get_best_format(info_dict: Dict[str, Any]) -> str:
    """Select best audio format"""
    formats = info_dict.get("formats", [])
    if not formats:
        return "bestaudio/best"
    
    # Score formats
    scored_formats = []
    for fmt in formats:
        score = 0
        
        # Audio codec preference
        acodec = fmt.get("acodec", "none")
        if acodec != "none":
            if "mp4a" in acodec or "aac" in acodec:
                score += 30
            elif "opus" in acodec:
                score += 25
            elif "vorbis" in acodec:
                score += 20
            else:
                score += 10
        else:
            continue  # Skip video-only formats
        
        # Video codec (prefer audio-only)
        vcodec = fmt.get("vcodec", "none")
        if vcodec == "none":
            score += 50  # Audio-only bonus
        
        # Bitrate preference
        abr = fmt.get("abr") or fmt.get("tbr") or 0
        score += min(abr, 320) * 0.1  # Cap at 320kbps
        
        # Container preference
        ext = fmt.get("ext", "").lower()
        if ext in ["m4a", "aac"]:
            score += 20
        elif ext in ["webm", "ogg"]:
            score += 15
        elif ext == "mp4":
            score += 10
        
        scored_formats.append((score, fmt))
    
    if scored_formats:
        # Sort by score and return best
        scored_formats.sort(key=lambda x: x[0], reverse=True)
        best_fmt = scored_formats[0][1]
        format_id = best_fmt.get("format_id", "bestaudio")
        print(f"[format] Selected: {format_id} ({best_fmt.get('ext')}, {best_fmt.get('abr', 'unknown')}kbps)")
        return format_id
    
    return "bestaudio/best"

# --------- Core Download Function ---------
def download_video(url: str) -> str:
    """Enhanced download function with comprehensive bot bypass"""
    
    if not url or not is_valid_youtube_url(url):
        raise ValueError("Geçerli bir YouTube URL'si gereklidir")
    
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError("Video ID çıkarılamadı")
    
    # Check if this URL recently failed
    current_time = time.time()
    if video_id in failed_urls:
        last_fail_time, attempts = failed_urls[video_id]
        if current_time - last_fail_time < 1800 and attempts >= 3:  # 30 min cooldown
            raise RuntimeError("Bu video kısa süre önce birden fazla kez başarısız oldu. Lütfen daha sonra deneyin.")
    
    print(f"[TARGET] Video ID: {video_id}")
    print(f"[TARGET] URL: {url}")
    
    # Get fresh cookies
    cookiefile = get_fresh_cookies()
    
    # Define extraction strategies (order matters)
    strategies = [
        # Strategy: (name, client, use_po_token, aggressive, delay)
        ("Smart TV", "tv", False, False, 0),
        ("Android TV", "android_testsuite", False, False, 2),
        ("iOS Mobile", "ios", False, False, 3),
        ("Web Client", "web", False, True, 4),
        ("Android Creator", "android_creator", False, True, 5),
        ("Mobile Web", "mweb", False, True, 6),
        ("Android with PO Token", "android", True, True, 8),
        ("TV with PO Token", "tv", True, True, 10),
        ("Media Connect", "mediaconnect", False, True, 12),
        ("Web Embedded", "web_embedded", False, True, 15),
    ]
    
    last_error = None
    
    for i, (name, client, use_po_token, aggressive, base_delay) in enumerate(strategies, 1):
        print(f"\n[STRATEGY] {i}/{len(strategies)}: {name}")
        
        # Progressive delay with jitter
        if i > 1:
            delay = base_delay + random.uniform(1.0, 3.0)
            print(f"  [WAIT] {delay:.1f}s delay before attempt...")
            time.sleep(delay)
        
        try:
            # Step 1: Extract info
            print(f"  [INFO] Extracting video information...")
            info_opts = build_ytdl_opts(client, cookiefile, use_po_token, aggressive)
            
            with YoutubeDL(info_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    raise ExtractorError("Video bilgileri alınamadı")
                
                # Validate video accessibility
                availability = info.get("availability")
                if availability in ["private", "premium_only", "subscriber_only", "needs_auth", "unavailable"]:
                    raise ExtractorError(f"Video erişilemez: {availability}")
                
                if info.get("is_live"):
                    raise ExtractorError("Canlı yayın desteklenmiyor")
                
                title = info.get("title", "Unknown")[:50]
                duration = info.get("duration", 0)
                print(f"  [SUCCESS] '{title}' ({duration}s)")
                
                # Select best format
                format_id = get_best_format(info)
            
            # Step 2: Download with small delay
            inter_delay = 2.0 + random.uniform(0.5, 1.5)
            print(f"  [WAIT] {inter_delay:.1f}s before download...")
            time.sleep(inter_delay)
            
            # Download
            print(f"  [DOWNLOAD] Starting download...")
            dl_opts = build_ytdl_opts(client, cookiefile, use_po_token, aggressive)
            dl_opts["format"] = format_id
            
            # Track existing files
            files_before = set(os.listdir(DOWNLOAD_DIR)) if os.path.exists(DOWNLOAD_DIR) else set()
            
            with YoutubeDL(dl_opts) as ydl:
                ydl.download([url])
            
            # Find new file
            files_after = set(os.listdir(DOWNLOAD_DIR)) if os.path.exists(DOWNLOAD_DIR) else set()
            new_files = files_after - files_before
            
            if new_files:
                # Get most recent file
                newest = max(new_files, key=lambda f: os.path.getmtime(os.path.join(DOWNLOAD_DIR, f)))
                file_size = os.path.getsize(os.path.join(DOWNLOAD_DIR, newest))
                print(f"  [SUCCESS] {newest} ({file_size // 1024}KB)")
                
                # Clear failed attempts for this video
                if video_id in failed_urls:
                    del failed_urls[video_id]
                
                return newest
            
            # Fallback filename generation
            title_safe = re.sub(r'[^\w\s-]', '', title).strip()[:50]
            ext = "mp3" if ffmpeg_available() else (info.get("ext") or "m4a")
            fallback_name = f"{title_safe}.{ext}"
            print(f"  [FALLBACK] Generated filename: {fallback_name}")
            return fallback_name
            
        except Exception as e:
            last_error = e
            error_str = str(e).lower()
            print(f"  [FAILED] {type(e).__name__}: {str(e)[:100]}...")
            
            # Specific error handling
            if "player response" in error_str:
                print("    → Player response issue detected")
                if cookiefile and i <= 5:
                    # Try refreshing cookies
                    print("    → Attempting cookie refresh...")
                    cookiefile = get_fresh_cookies()
                    time.sleep(5 + random.uniform(1, 3))
            
            elif any(x in error_str for x in ["sign in", "bot", "captcha"]):
                print("    → Bot detection triggered")
                if i <= 6:
                    time.sleep(10 + random.uniform(2, 5))
            
            elif any(x in error_str for x in ["private", "unavailable", "deleted"]):
                print("    → Video permanently unavailable")
                break  # No point continuing
            
            elif any(x in error_str for x in ["rate", "quota", "limit"]):
                print("    → Rate/quota limit hit")
                time.sleep(20 + random.uniform(5, 10))
            
            elif any(x in error_str for x in ["network", "timeout", "connection"]):
                print("    → Network/connectivity issue")
                time.sleep(5)
            
            continue
    
    # All strategies failed - record failure
    if video_id:
        if video_id in failed_urls:
            failed_urls[video_id] = (current_time, failed_urls[video_id][1] + 1)
        else:
            failed_urls[video_id] = (current_time, 1)
    
    # Generate helpful error message
    error_msg = str(last_error) if last_error else "Bilinmeyen hata"
    error_lower = error_msg.lower()
    
    if "player response" in error_lower:
        detailed_error = (
            f"Player Response Hatası: {error_msg}\n\n"
            "🔧 ÇÖZÜMLer:\n"
            "• YouTube'da oturum açın ve fresh cookies.txt yükleyin\n"
            "• Kaliteli residential proxy kullanın (YTDLP_PROXY)\n"
            "• PO Token ve Visitor Data ekleyin\n"
            "• 30-60 dakika bekleyip farklı IP'den deneyin"
        )
    elif any(x in error_lower for x in ["bot", "sign in", "captcha"]):
        detailed_error = (
            f"Bot Detection: {error_msg}\n\n"
            "🤖 ÇÖZÜMLer:\n"
            "• Chrome'dan güncel cookies.txt export edin\n"
            "• Residential proxy kullanın (datacenter/VPN değil)\n"
            "• Farklı lokasyon/IP deneyin\n"
            "• 15-30 dakika ara verin"
        )
    elif any(x in error_lower for x in ["private", "unavailable"]):
        detailed_error = f"Video Erişilemez: {error_msg}\n\nVideo özel, silinmiş veya coğrafi olarak engellenmiş."
    elif any(x in error_lower for x in ["rate", "limit", "quota"]):
        detailed_error = f"Rate Limit: {error_msg}\n\nYouTube indirme limitiniz aşıldı. 30-60 dakika bekleyin."
    else:
        detailed_error = (
            f"Genel Hata: {error_msg}\n\n"
            "💡 ÇÖZÜMLer:\n"
            "• URL'nin doğru olduğunu kontrol edin\n"
            "• yt-dlp güncelleyin: pip install -U yt-dlp\n"
            "• Cookies ve proxy ayarlarını kontrol edin"
        )
    
    raise RuntimeError(detailed_error)

# --------- Flask Routes ---------
@app.errorhandler(413)
def too_large(e):
    return jsonify(error="Dosya çok büyük. Maksimum 16MB."), 413

@app.errorhandler(500)
def internal_error(e):
    return jsonify(error="Sunucu hatası. Lütfen tekrar deneyin."), 500

@app.get("/health")
def health():
    """Enhanced health check"""
    cookie_status = bool(get_fresh_cookies())
    
    return jsonify(
        ok=True,
        ffmpeg=ffmpeg_available(),
        download_dir=DOWNLOAD_DIR,
        proxy=bool(PROXY),
        cookies=cookie_status,
        disk_free_gb=shutil.disk_usage(DOWNLOAD_DIR).free // (1024**3) if os.path.exists(DOWNLOAD_DIR) else 0,
        active_sessions=len(download_sessions),
        failed_videos=len(failed_urls)
    )

@app.get("/cookie_check")
def cookie_check():
    """Enhanced cookie validation"""
    cookiefile = get_fresh_cookies()
    
    if not cookiefile or not os.path.exists(cookiefile):
        return jsonify(ok=False, reason="cookies.txt dosyası bulunamadı"), 404

    try:
        youtube_cookies = 0
        important_cookies = set()
        total_lines = 0
        
                        with open(cookiefile, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    total_lines += 1
                    parts = line.split('\t')
                    if len(parts) >= 6 and ("youtube.com" in parts[0] or ".youtube." in parts[0]):
                        youtube_cookies += 1
                        cookie_name = parts[5] if len(parts) > 5 else ""
                        if cookie_name in ["SID", "__Secure-3PSID", "SAPISID", "APISID", "HSID", "SSID", "CONSENT", "VISITOR_INFO1_LIVE", "YSC"]:
                            important_cookies.add(cookie_name)

        required_cookies = {"SID", "__Secure-3PSID", "SAPISID", "APISID"}
        missing_required = required_cookies - important_cookies
        
        return jsonify(
            ok=True,
            total_lines=total_lines,
            youtube_cookies=youtube_cookies,
            important_cookies_found=sorted(list(important_cookies)),
            missing_required=sorted(list(missing_required)),
            file_size_bytes=os.path.getsize(cookiefile),
            is_valid=len(missing_required) == 0
        )
    except Exception as e:
        return jsonify(ok=False, reason=f"Cookie dosyası okunamıyor: {str(e)}"), 500

@app.route("/", methods=["GET", "POST"])
def index():
    """Main page with enhanced error handling"""
    if request.method == "POST":
        # Enhanced rate limiting
        client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', 
                                      request.environ.get('HTTP_X_REAL_IP', 
                                                        request.remote_addr))
        
        allowed, attempts = check_rate_limit(client_ip)
        if not allowed:
            msg_html = f'<div class="msg err">Rate limit aşıldı. 15 dakika içinde maksimum 5 indirme yapabilirsiniz. (Mevcut: {attempts})</div>'
            content = FORM_CONTENT.format(url="", msg_block=msg_html)
            return render_template_string(HTML_SHELL.replace("<!--CONTENT-->", content)), 429

        url = (request.form.get("url") or "").strip()
        
        # Handle cookie file upload with validation
        uploaded_file = request.files.get("cookies")
        if uploaded_file and uploaded_file.filename:
            try:
                file_content = uploaded_file.read()
                
                # Validate file size
                if len(file_content) > 2 * 1024 * 1024:  # 2MB limit
                    raise ValueError("Cookie dosyası çok büyük (>2MB)")
                
                # Basic validation for cookie file format
                content_str = file_content.decode('utf-8', errors='ignore')
                if not any("youtube.com" in line for line in content_str.split('\n')[:50]):
                    raise ValueError("Geçerli YouTube cookies.txt dosyası değil")
                
                # Save to tmp location
                with open("/tmp/cookies.txt", "wb") as f:
                    f.write(file_content)
                
                print(f"[cookie] Uploaded fresh cookies from {client_ip}")
                
            except Exception as e:
                msg_html = f'<div class="msg err">Cookie dosyası hatası: {str(e)}</div>'
                content = FORM_CONTENT.format(url=url, msg_block=msg_html)
                return render_template_string(HTML_SHELL.replace("<!--CONTENT-->", content)), 400

        try:
            filename = download_video(url)
            return redirect(url_for("done", filename=filename))
        except Exception as e:
            error_msg = str(e)
            
            # Sanitize and truncate error message for display
            if len(error_msg) > 1000:
                error_msg = error_msg[:997] + "..."
            
            # HTML escape to prevent XSS
            error_msg = error_msg.replace("<", "&lt;").replace(">", "&gt;")
            
            msg_html = f'<div class="msg err">{error_msg}</div>'
            content = FORM_CONTENT.format(url=url, msg_block=msg_html)
            return render_template_string(HTML_SHELL.replace("<!--CONTENT-->", content)), 400

    # GET: show empty form
    content = FORM_CONTENT.format(url="", msg_block="")
    return render_template_string(HTML_SHELL.replace("<!--CONTENT-->", content))

@app.get("/done")
def done():
    """Success page with enhanced validation"""
    filename = request.args.get("filename")
    if not filename:
        return redirect(url_for("index"))
    
    # Security: prevent directory traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        return redirect(url_for("index"))
    
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        msg_html = '<div class="msg err">Dosya bulunamadı. Lütfen tekrar indirin.</div>'
        content = FORM_CONTENT.format(url="", msg_block=msg_html)
        return render_template_string(HTML_SHELL.replace("<!--CONTENT-->", content)), 404
    
    # Escape filename for JavaScript
    safe_filename = filename.replace("'", "\\'").replace('"', '\\"').replace("\\", "\\\\")
    content = DONE_CONTENT.format(filename=safe_filename)
    return render_template_string(HTML_SHELL.replace("<!--CONTENT-->", content))

@app.route("/download/<path:filename>")
def download_file(filename):
    """Secure file download with enhanced validation"""
    # Security checks
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        return "Geçersiz dosya adı", 400
    
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    
    # Validate file exists and is actually a file
    if not os.path.exists(file_path):
        return "Dosya bulunamadı", 404
    
    if not os.path.isfile(file_path):
        return "Geçersiz dosya", 400
    
    # Check file size (prevent serving huge files)
    file_size = os.path.getsize(file_path)
    if file_size > 500 * 1024 * 1024:  # 500MB limit
        return "Dosya çok büyük", 413
    
    try:
        return send_from_directory(
            DOWNLOAD_DIR, 
            filename, 
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        print(f"Download error for {filename}: {e}")
        return "Dosya indirilemedi", 500

@app.route("/stats")
def stats():
    """Statistics endpoint for monitoring"""
    return jsonify(
        active_sessions=len(download_sessions),
        failed_videos=len(failed_urls),
        download_dir_files=len(os.listdir(DOWNLOAD_DIR)) if os.path.exists(DOWNLOAD_DIR) else 0,
        disk_usage_gb={
            "total": shutil.disk_usage(DOWNLOAD_DIR).total // (1024**3),
            "used": shutil.disk_usage(DOWNLOAD_DIR).used // (1024**3), 
            "free": shutil.disk_usage(DOWNLOAD_DIR).free // (1024**3)
        } if os.path.exists(DOWNLOAD_DIR) else None
    )

@app.route("/cleanup", methods=["POST"])
def cleanup_files():
    """Enhanced cleanup with better safety"""
    try:
        files_removed = 0
        total_size_removed = 0
        current_time = time.time()
        
        if os.path.exists(DOWNLOAD_DIR):
            for filename in os.listdir(DOWNLOAD_DIR):
                file_path = os.path.join(DOWNLOAD_DIR, filename)
                
                if os.path.isfile(file_path):
                    file_age = current_time - os.path.getmtime(file_path)
                    
                    # Remove files older than 2 hours
                    if file_age > 7200:
                        try:
                            file_size = os.path.getsize(file_path)
                            os.remove(file_path)
                            files_removed += 1
                            total_size_removed += file_size
                        except Exception as e:
                            print(f"Failed to remove {filename}: {e}")
        
        return jsonify(
            ok=True,
            files_removed=files_removed,
            size_removed_mb=total_size_removed // (1024 * 1024)
        )
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

# Background cleanup with improved error handling
def background_cleanup():
    """Enhanced background cleanup"""
    def cleanup_worker():
        while True:
            try:
                current_time = time.time()
                files_cleaned = 0
                size_cleaned = 0
                
                # Clean download directory
                if os.path.exists(DOWNLOAD_DIR):
                    for filename in os.listdir(DOWNLOAD_DIR):
                        file_path = os.path.join(DOWNLOAD_DIR, filename)
                        try:
                            if os.path.isfile(file_path):
                                file_age = current_time - os.path.getmtime(file_path)
                                if file_age > 10800:  # 3 hours
                                    file_size = os.path.getsize(file_path)
                                    os.remove(file_path)
                                    files_cleaned += 1
                                    size_cleaned += file_size
                        except Exception as e:
                            print(f"[cleanup] Error removing {filename}: {e}")
                
                # Clean failed URLs cache (older than 24 hours)
                expired_videos = []
                for video_id, (fail_time, attempts) in failed_urls.items():
                    if current_time - fail_time > 86400:  # 24 hours
                        expired_videos.append(video_id)
                
                for video_id in expired_videos:
                    del failed_urls[video_id]
                
                # Clean old download sessions (older than 1 hour)  
                expired_ips = []
                for ip, sessions in download_sessions.items():
                    download_sessions[ip] = [
                        timestamp for timestamp in sessions 
                        if current_time - timestamp < 3600
                    ]
                    if not download_sessions[ip]:
                        expired_ips.append(ip)
                
                for ip in expired_ips:
                    del download_sessions[ip]
                
                if files_cleaned > 0 or expired_videos or expired_ips:
                    print(f"[cleanup] Removed {files_cleaned} files ({size_cleaned // 1024}KB), "
                          f"{len(expired_videos)} expired URLs, {len(expired_ips)} old sessions")
                
            except Exception as e:
                print(f"[cleanup] Background cleanup error: {e}")
            
            time.sleep(1800)  # Run every 30 minutes
    
    cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
    cleanup_thread.start()

# Initialize background processes
background_cleanup()

if __name__ == "__main__":
    # Enhanced startup validation
    print("[STARTUP] YouTube to MP3 Converter başlatılıyor...")
    print(f"[CONFIG] Download dizini: {DOWNLOAD_DIR}")
    print(f"[CONFIG] FFmpeg mevcut: {ffmpeg_available()}")
    print(f"[CONFIG] Proxy: {'Evet' if PROXY else 'Hayır'}")
    
    # Check cookies
    cookie_file = get_fresh_cookies()
    print(f"[CONFIG] Cookies: {'Evet' if cookie_file else 'Hayır'}")
    
    # Test write permissions
    try:
        test_file = os.path.join(DOWNLOAD_DIR, "test_write.tmp")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        print("[OK] Yazma izinleri OK")
    except Exception as e:
        print(f"[WARN] Yazma izin problemi: {e}")
    
    # Check yt-dlp version
    try:
        import subprocess
        result = subprocess.run(['yt-dlp', '--version'], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print(f"[VERSION] yt-dlp: {result.stdout.strip()}")
        else:
            print("[WARN] yt-dlp version check failed")
    except Exception as e:
        print(f"[WARN] Could not check yt-dlp version: {e}")
    
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "False").lower() == "true"
    
    print(f"[SERVER] Starting on http://0.0.0.0:{port}")
    
    try:
        app.run(
            host="0.0.0.0", 
            port=port, 
            debug=debug, 
            threaded=True,
            use_reloader=False  # Prevent double startup in debug mode
        )
    except KeyboardInterrupt:
        print("\n[EXIT] Shutting down gracefully...")
    except Exception as e:
        print(f"[ERROR] Server error: {e}")