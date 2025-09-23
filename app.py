msg_html = f'<div class="msg err">[ERROR] Indirme Hatasi: {error_msg}</div>'# -*- coding: utf-8 -*-
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
import random
from typing import Optional, Dict, Any, List, Tuple

from flask import Flask, request, send_from_directory, render_template_string, jsonify, redirect, url_for
try:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError
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
    input[type=text]{width:100%;padding:12px;border:1px solid #cbd5e1;border-radius:10px;box-sizing:border-box}
    .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px}
    input[type=file]{flex:1}
    button,a.btn{padding:10px 16px;border:0;border-radius:10px;background:var(--primary);color:#fff;cursor:pointer;text-decoration:none;display:inline-block;box-sizing:border-box}
    button[disabled],a.btn.disabled{opacity:.6;pointer-events:none}
    .msg{margin-top:14px;white-space:pre-wrap}
    .ok{background:var(--okbg);color:#14532d;padding:12px;border-radius:8px}
    .err{background:var(--errbg);color:var(--err);padding:12px;border-radius:8px}
    .note{margin-top:16px;font-size:.95em;color:var(--muted)}
    .divider{height:1px;background:#e5e7eb;margin:20px 0}
    .countdown{font-size:0.9em;color:#666;margin-top:8px}
    .progress{margin-top:12px;background:#f3f4f6;border-radius:4px;overflow:hidden}
    .progress-bar{height:6px;background:var(--ok);width:0;transition:width 0.3s ease}
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
      
      // Re-enable after 30 seconds to prevent permanent disable
      setTimeout(() => {{
        btn.disabled = false;
        btn.textContent = 'İndir';
      }}, 30000);
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
        // Dosyayı indir
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        
        // Butonu güncelle
        const dlBtn = document.getElementById('dlbtn');
        if (dlBtn) {{
          dlBtn.textContent = 'İndiriliyor...';
          dlBtn.classList.add('disabled');
        }}
        
        // 3 saniye sonra ana sayfaya yönlendir
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
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file upload

# --------- Helpers ---------
def ffmpeg_available() -> bool:
    """Check if FFmpeg is available in PATH"""
    return shutil.which("ffmpeg") is not None

def is_valid_youtube_url(url: str) -> bool:
    """Validate YouTube URL with improved regex"""
    if not url:
        return False
    
    # Normalize URL - add https if missing
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    patterns = [
        r'(?:https?://)?(?:www\.)?(?:youtube|youtu|youtube-nocookie)\.(?:com|be)/'
        r'(?:watch\?v=|embed/|v/|.+\?v=|shorts/)?([A-Za-z0-9_-]{11})(?:\S+)?',
        r'(?:https?://)?(?:m\.)?youtube\.com/shorts/([A-Za-z0-9_-]{11})',
        r'(?:https?://)?youtu\.be/([A-Za-z0-9_-]{11})'
    ]
    
    for pattern in patterns:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    return False

def check_rate_limit(ip: str) -> bool:
    """Check if IP is rate limited. Returns True if allowed, False if blocked."""
    if not ip:
        return True
        
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
            "./cookies.txt",  # Current directory
        ]
        
        for src in candidates:
            if src and os.path.exists(src) and os.path.getsize(src) > 0:
                try:
                    shutil.copyfile(src, tmp)
    print(f"[cookie] {'refreshed' if refresh else 'copied'} {src} -> {tmp}")
                    return tmp
                except Exception as e:
                    print(f"[cookie] failed to copy {src}: {e}")
        
        if refresh:
            print("[cookie] refresh failed - no valid sources found")
        else:
            print("[cookie] not found")
        return None
    
    print("[cookie] using existing /tmp/cookies.txt")
    return tmp

def build_opts(*, player_clients, cookiefile: Optional[str] = None, proxy: Optional[str] = PROXY, postprocess: bool = True, use_po_token: bool = False, aggressive_bypass: bool = False) -> Dict[str, Any]:
    """Build yt-dlp options with enhanced error handling"""
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
        "http_chunk_size": 262144 if aggressive_bypass else 524288,
        "source_address": "0.0.0.0",
        "sleep_interval_requests": 2 if aggressive_bypass else 1,
        "max_sleep_interval": 8 if aggressive_bypass else 3,
        "http_headers": {
            "User-Agent": selected_ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
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
    
    if cookiefile and os.path.exists(cookiefile):
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
    """Choose best audio format with improved logic"""
    fmts = info.get("formats") or []
    if not fmts:
        return "bestaudio/best"
        
    candidates: List[Tuple[float, Dict[str, Any]]] = []
    
    for f in fmts:
        acodec = f.get("acodec")
        vcodec = f.get("vcodec")
        
        # Skip video-only formats
        if not acodec or acodec == "none":
            continue
            
        is_audio_only = (vcodec in (None, "none"))
        abr = f.get("abr") or f.get("tbr") or 0
        ext = (f.get("ext") or "").lower()
        
        # Scoring system
        ext_bonus = 30 if ext == "m4a" else (20 if ext == "webm" else (10 if ext == "mp4" else 0))
        audio_only_bonus = 50 if is_audio_only else 0
        quality_score = min(abr or 0, 320)  # Cap at 320 kbps
        
        total_score = quality_score + audio_only_bonus + ext_bonus
        candidates.append((total_score, f))
    
    if not candidates:
        return "bestaudio/best"
    
    # Sort by score and return best format
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_format = candidates[0][1]
    format_id = best_format.get("format_id")
    
    print(f"[format] Selected: {format_id} ({best_format.get('ext')}, {best_format.get('abr')}kbps)")
    return format_id or "bestaudio/best"

# --------- Core Download Function ---------
def run_download(url: str) -> str:
    """Main download function with 2024 enhanced anti-bot bypass"""
    if not url:
        raise ValueError("URL boş olamaz.")
    if not is_valid_youtube_url(url):
        raise ValueError("Geçerli bir YouTube URL'si giriniz.")

    # Pre-flight checks and setup
    print(f"[TARGET] URL: {url}")
    cookie = ensure_cookiefile(refresh=False)
    cookie_refreshed = False
    
    # Check if yt-dlp is up to date
    try:
        import subprocess
        result = subprocess.run(['yt-dlp', '--version'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print(f"[VERSION] yt-dlp version: {result.stdout.strip()}")
    except:
        print("[WARN] Could not check yt-dlp version")

    # CRITICAL 2024 FIX: YouTube requires OAuth/PO-Token for most videos
    # Try alternative extraction methods first
    alternative_strategies = [
        # Use yt-dlp's emergency extraction modes
        ("Emergency TV Client", ["tv"], False, True, 1, {"extractor_args": {"youtube": {"player_client": "tv", "innertube_host": "youtubei.googleapis.com", "innertube_key": "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"}}}),
        ("Android Testsuite", ["android_testsuite"], False, True, 2, {}),
        ("Web with Bypass", ["web"], False, True, 3, {"extractor_args": {"youtube": {"skip": ["dash", "hls"], "player_skip": ["configs"]}}}),
        ("iOS Safari", ["ios"], False, True, 4, {}),
        ("Media Connect", ["mediaconnect"], False, True, 5, {}),
    ]

    # Standard 2024 Bypass Strategies 
    standard_strategies = [
        ("Smart TV (Primary)", ["tv"], False, True, 1, {}),
        ("Android Creator", ["android_creator"], False, True, 2, {}), 
        ("Mobile Web", ["mweb"], False, True, 3, {}),
        ("PO Token + Android", ["android"], True, True, 4, {}),
        ("iOS Music", ["ios_music"], False, True, 5, {}),
    ]
    
    # Combine strategies: try emergency first, then standard
    all_strategies = alternative_strategies + standard_strategies

    last_err = None
    for idx, (name, clients, use_po_token, aggressive_bypass, base_delay, extra_opts) in enumerate(all_strategies, start=1):
        print(f"\n[STRATEGY] {idx}/{len(all_strategies)}: {name} -> {','.join(clients)}")
        
        # Progressive delay with jitter to avoid detection patterns
        if idx > 1:
            delay = base_delay + (idx * 0.7) + (random.uniform(0.5, 1.5))
            print(f"  [DELAY] {delay:.1f}s wait...")
            time.sleep(delay)
        
        try:
            # Step 1: Information extraction with enhanced bypass
            print(f"  [EXTRACT] Video metadata extraction...")
            opts_info = build_opts(
                player_clients=clients, 
                cookiefile=cookie, 
                postprocess=False, 
                use_po_token=use_po_token,
                aggressive_bypass=aggressive_bypass
            )
            
            # Apply extra options for specific strategies
            opts_info.update(extra_opts)
            
            # Add additional bypass for problematic videos
            if idx > 5:  # More aggressive for later strategies
                opts_info["sleep_interval_requests"] = 5
                opts_info["max_sleep_interval"] = 15
                opts_info["socket_timeout"] = 120
                # Try without cookies for some strategies
                if "cookiefile" in opts_info and idx > 7:
                    del opts_info["cookiefile"]
                    print("  [NO-COOKIE] Trying without cookies...")
            
            # CRITICAL: Try with minimal extraction first for player response issues
            if "player response" in str(last_err).lower() and idx > 3:
                opts_info["extract_flat"] = False
                opts_info["skip_download"] = True
                print("  [MINIMAL] Using minimal extraction mode...")
            
            with YoutubeDL(opts_info) as extractor:
                info = extractor.extract_info(url, download=False)
                
                if not info:
                    raise DownloadError("Video metadata extraction failed")
                    
                # Validate video availability
                if info.get("is_live"):
                    raise DownloadError("Live streams are not supported")
                    
                availability = info.get("availability")
                if availability in ["private", "premium_only", "subscriber_only", "needs_auth", "unavailable"]:
                    raise DownloadError(f"Video is not accessible: {availability}")
                
                # Age restriction check
                if info.get("age_limit", 0) > 0:
                    print(f"  [AGE] Age-restricted content (limit: {info.get('age_limit')})")
                    if not cookie:
                        print("  [WARN] Age restriction detected but no cookies available")
                
                # Format selection
                fmt = choose_format(info)
                title = info.get("title", "Unknown")[:50]
                duration = info.get("duration", 0)
                print(f"  [OK] '{title}' ({duration}s, format: {fmt})")

            # Critical wait between extraction and download
            inter_delay = 2.0 + (idx * 0.3) + random.uniform(0.2, 0.8)
            print(f"  [WAIT] Inter-step wait: {inter_delay:.1f}s")
            time.sleep(inter_delay)

            # Step 2: Actual download with same configuration
            print(f"  [DL] Starting download...")
            opts_download = build_opts(
                player_clients=clients, 
                cookiefile=cookie, 
                postprocess=True, 
                use_po_token=use_po_token,
                aggressive_bypass=aggressive_bypass
            )
            opts_download.update(extra_opts)
            opts_download["format"] = fmt
            
            # Additional download optimizations for difficult cases
            if idx > 3:
                opts_download["concurrent_fragment_downloads"] = 1
                opts_download["http_chunk_size"] = 65536  # Very small chunks
                opts_download["fragment_retries"] = 10
                
            # Remove cookies for download if extraction succeeded without them
            if "cookiefile" not in opts_info and "cookiefile" in opts_download:
                del opts_download["cookiefile"]
                print("  🚫 Download without cookies (matched extraction)")
            
            # Track files before download
            files_before = set(os.listdir(DOWNLOAD_DIR)) if os.path.exists(DOWNLOAD_DIR) else set()
            
            with YoutubeDL(opts_download) as downloader:
                downloader.download([url])
            
            # Check for new files
            files_after = set(os.listdir(DOWNLOAD_DIR)) if os.path.exists(DOWNLOAD_DIR) else set()
            new_files = files_after - files_before
            
            if new_files:
                # Get the most recent file (should be our download)
                newest_file = max(
                    new_files,
                    key=lambda f: os.path.getmtime(os.path.join(DOWNLOAD_DIR, f))
                )
                file_size = os.path.getsize(os.path.join(DOWNLOAD_DIR, newest_file))
                print(f"  🎉 SUCCESS: {newest_file} ({file_size // 1024}KB)")
                return newest_file

            # Fallback: generate expected filename
            title_clean = "".join(c for c in (info.get("title") or "audio") if c.isalnum() or c in " ._-")[:50].strip()
            ext = "mp3" if ffmpeg_available() else (info.get("ext") or "m4a")
            fallback_name = f"{title_clean}.{ext}"
            print(f"  📝 Using fallback filename: {fallback_name}")
            return fallback_name

        except Exception as e:
            last_err = e
            error_msg = str(e).lower()
            print(f"❌ Strategy {idx} failed: {type(e).__name__}: {str(e)[:100]}...")
            
            # Enhanced error handling with specific recovery strategies
            if "failed to extract any player response" in error_msg:
                print("  🔍 Player response failure - trying alternative approach")
                if not cookie_refreshed and idx <= 6:
                    print("  🔄 Refreshing cookies...")
                    cookie = ensure_cookiefile(refresh=True)
                    cookie_refreshed = True
                    # Try a longer wait with exponential backoff
                    wait_time = min(30, 8 + (idx * 2))
                    print(f"     Extended wait: {wait_time}s")
                    time.sleep(wait_time)
                    
            elif "sign in to confirm" in error_msg or "bot" in error_msg:
                print("  🤖 Bot detection - need fresh session")
                if not cookie_refreshed and idx <= 4:
                    cookie = ensure_cookiefile(refresh=True)
                    cookie_refreshed = True
                    time.sleep(6 + random.uniform(1, 3))
                    
            elif any(keyword in error_msg for keyword in ["private", "unavailable", "removed", "deleted"]):
                print("  🚫 Video is permanently unavailable")
                break  # No point trying other strategies
                
            elif any(keyword in error_msg for keyword in ["rate", "limit", "quota", "too many requests"]):
                print("  ⏳ Rate/quota limit hit - extended backoff")
                wait_time = 15 + (idx * 3) + random.uniform(5, 10)
                print(f"     Waiting {wait_time:.1f}s...")
                time.sleep(wait_time)
                
            elif any(keyword in error_msg for keyword in ["network", "timeout", "connection", "resolve"]):
                print("  🌐 Network/connection issue")
                time.sleep(3 + random.uniform(1, 2))
                
            elif any(keyword in error_msg for keyword in ["age", "restricted"]):
                print("  🔞 Age restriction - cookies essential")
                if not cookie_refreshed:
                    cookie = ensure_cookiefile(refresh=True)
                    cookie_refreshed = True
                    time.sleep(4)
                    
            elif "format" in error_msg:
                print("  🎵 Format selection issue - will try different strategy")
            
            continue

    # All strategies exhausted - provide comprehensive error report
    final_error = str(last_err) if last_err else "Unknown error occurred"
    error_lower = final_error.lower()
    
    # Generate specific troubleshooting advice
    troubleshooting = ""
    if "failed to extract any player response" in error_lower:
        troubleshooting = (
            "\n\n[FIX] PLAYER RESPONSE ERROR - KRITIK COZUMLER:"
            "\n[INFO] Bu hata YouTube'un 2024 sonrasi sikti anti-bot korumasindan kaynaklanir."
            "\n"
            "\n[QUICK] HIZLI COZUMLER:"
            "\n• yt-dlp guncelle: pip install -U yt-dlp"  
            "\n• Youtube'da giris yap → F12 → Application → Cookies → youtube.com → Tumunu kopyala"
            "\n• Kopyalanan cookies'i cookies.txt olarak kaydet ve yukle"
            "\n• VPN/proxy kullan: YTDLP_PROXY=http://proxy:port"
            "\n"
            "\n[ADVANCED] GELISMIS COZUMLER:"
            "\n• PO Token al: Browser → F12 → Network → youtube.com → Request Headers'dan"
            "\n• YTDLP_PO_TOKEN ve YTDLP_VISITOR_DATA environment variables set et"
            "\n• Residential proxy kullan (datacenter proxy degil)"
            "\n• 30-60 dakika bekle, farkli IP/lokasyondan dene"
            "\n"
            "\n[LAST] SON CARE:"
            "\n• Video URL'sini farkli bir YouTube downloader ile dene"
            "\n• youtube-dl yerine yt-dlp development version kullan"
            "\n• Video sahipinden direkt linki iste"
        )
    elif any(keyword in error_lower for keyword in ["bot", "sign in to confirm"]):
        troubleshooting = (
            "\n\n[BOT] BOT DETECTION - DOGRULAMA GEREKIYOR:"
            "\n• Chrome'da YouTube'a giris yap"
            "\n• F12 → Application → Cookies → youtube.com → Export all to cookies.txt"
            "\n• Cookies.txt dosyasini uygulamaya yukle"
            "\n• Residential proxy kullan (datacenter/VPN degil)"
            "\n• Farkli IP/lokasyon dene"
            "\n• Denemeler arasinda 15-30 dakika bekle"
        )
    elif any(keyword in error_lower for keyword in ["private", "unavailable"]):
        troubleshooting = (
            "\n\n[UNAVAIL] VIDEO ERISILEMEZ:"
            "\n• Video ozel, silinmis veya cografi olarak engellenmis"
            "\n• Video sahibinin ayarlarini kontrol edin"
            "\n• VPN ile farkli ulkeden deneyin"
        )
    elif any(keyword in error_lower for keyword in ["rate", "limit"]):
        troubleshooting = (
            "\n\n[RATE] RATE LIMIT ASILDI:"
            "\n• YouTube indirme limitiniz doldu"
            "\n• 30-60 dakika bekleyin"
            "\n• Farkli IP/network kullanin"
            "\n• Proxy kullanarak deneyin"
        )
    else:
        troubleshooting = (
            "\n\n[GENERAL] GENEL SORUN GIDERME:"
            "\n• URL'nin dogru ve erisilebilir oldugunu kontrol edin"
            "\n• Internet baglantinizi kontrol edin"
            "\n• yt-dlp guncelleyin: pip install -U yt-dlp"
            "\n• Cookies.txt ve proxy ayarlarini kontrol edin"
            "\n• 10-15 dakika sonra tekrar deneyin"
            "\n• Farkli bir video ile test edin"
        )
    
    raise RuntimeError(f"Tüm bypass stratejileri başarısız oldu.\n\nSon hata: {final_error}{troubleshooting}")
        ("Android TV", ["android_creator"], False, True, 1), 
        ("Mobile Web", ["mweb"], False, True, 2),
        ("PO Token + Android", ["android"], True, True, 2),
        ("iOS Music", ["ios_music"], False, True, 3),
        ("TV Embedded", ["tv_embedded"], False, True, 3),
        ("Android Creator Studio", ["android_creator"], False, True, 4),
        ("Web Creator", ["web_creator"], False, True, 4),
        ("Legacy Android", ["android_legacy"], False, True, 5),
        ("All Bypass Mix", ["tv", "android_creator", "mweb"], False, True, 6),
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
                availability = info.get("availability")
                if availability in ["private", "premium_only", "subscriber_only", "needs_auth", "unavailable"]:
                    raise DownloadError(f"Video erişilemez durumda: {availability}")
                
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
                filename = new_files[0]
                file_path = os.path.join(DOWNLOAD_DIR, filename)
                file_size = os.path.getsize(file_path)
                print(f"  ✅ Başarılı: {filename} ({file_size // 1024}KB)")
                return filename

            # Fallback filename generation
            title = (info.get("title") or "audio").strip()
            ext = "mp3" if ffmpeg_available() else (info.get("ext") or "m4a")
            safe_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- "
            safe_title = "".join(c for c in title if c in safe_chars)[:50]
            safe_filename = f"{safe_title}.{ext}".strip()
            
            if safe_filename and len(safe_filename) > 4:
                return safe_filename

        except Exception as e:
            last_err = e
            error_msg = str(e).lower()
            print(f"❌ Strateji {idx} başarısız: {e}")
            
            # Specific error handling with 2024 workarounds
            if "failed to extract any player response" in error_msg:
                print("  🔍 Player response hatası - agresif bypass + cookie refresh")
                if not cookie_refreshed and idx <= 4:  # Try refresh on more strategies
                    print("  🔄 Cookie refresh + extended wait...")
                    cookie = ensure_cookiefile(refresh=True)
                    cookie_refreshed = True
                    time.sleep(8)  # Longer wait for player response issues
                    
            elif "sign in to confirm" in error_msg or "bot" in error_msg:
                print("  🤖 Bot detection - need fresh cookies + proxy")
                if not cookie_refreshed and idx <= 3:
                    cookie = ensure_cookiefile(refresh=True)
                    cookie_refreshed = True
                    time.sleep(6)
                    
            elif "private" in error_msg or "unavailable" in error_msg or "removed" in error_msg:
                print("  🚫 Video unavailable - skipping remaining strategies")
                break  # No point trying other strategies
                
            elif "rate" in error_msg or "limit" in error_msg or "quota" in error_msg:
                print("  ⏳ Rate/quota limit - extended wait")
                time.sleep(15)  # Longer wait for rate limits
                
            elif "network" in error_msg or "timeout" in error_msg or "connection" in error_msg:
                print("  🌐 Network issue - short wait")
                time.sleep(3)
                
            elif "age" in error_msg or "restricted" in error_msg:
                print("  🔞 Age restriction - cookies required")
                if not cookie_refreshed:
                    cookie = ensure_cookiefile(refresh=True)
                    cookie_refreshed = True
                    time.sleep(4)
            
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
        hint = "\n\n❌ Video özel, kaldırılmış veya coğrafi olarak engellenmiş."
    elif ("rate" in low) or ("limit" in low):
        hint = "\n\n⏳ Rate limit aşıldı. 15-30 dakika bekleyip tekrar deneyin."
    else:
        hint = ("\n\n💡 Genel Çözüm Önerileri:"
                "\n• Video URL'sinin doğru ve erişilebilir olduğundan emin olun"
                "\n• Cookies.txt ve proxy ayarlarını kontrol edin"
                "\n• Yt-dlp'nin güncel olduğundan emin olun")
    
    raise RuntimeError(f"Tüm bypass stratejileri başarısız: {msg}{hint}")

# --------- Flask Routes ---------
@app.errorhandler(413)
def too_large(e):
    return jsonify(error="Dosya çok büyük. Maksimum 16MB."), 413

@app.errorhandler(500)
def internal_error(e):
    return jsonify(error="Sunucu hatası. Lütfen tekrar deneyin."), 500

@app.get("/health")
def health():
    """Health check endpoint"""
    return jsonify(
        ok=True,
        ffmpeg=ffmpeg_available(),
        download_dir=DOWNLOAD_DIR,
        proxy=bool(PROXY),
        disk_free=shutil.disk_usage(DOWNLOAD_DIR).free // (1024**3) if os.path.exists(DOWNLOAD_DIR) else 0
    )

@app.get("/cookie_check")
def cookie_check():
    """Check cookie file validity"""
    path = "/tmp/cookies.txt"
    
    # Try to find and copy cookie file
    if not os.path.exists(path):
        secret_paths = [
            "/etc/secrets/cookies.txt",
            "/etc/secrets/COOKIES.txt",
            "/etc/secrets/youtube-cookies.txt",
            "/app/cookies.txt",
            "./cookies.txt"
        ]
        
        for secret_path in secret_paths:
            if os.path.exists(secret_path):
                try:
                    shutil.copyfile(secret_path, path)
                    break
                except Exception as e:
                    print(f"Failed to copy {secret_path}: {e}")
    
    if not os.path.exists(path):
        return jsonify(ok=False, reason="cookies.txt yok"), 404

    try:
        lines = []
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    lines.append(line)

        keys_present = set()
        youtube_lines = 0
        
        for line in lines:
            parts = line.split('\t')  # Cookie files use tab separation
            if len(parts) >= 7:
                domain = parts[0]
                cookie_name = parts[5]
                
                if "youtube.com" in domain or ".youtube." in domain:
                    youtube_lines += 1
                    keys_present.add(cookie_name)

        required = {"SID", "__Secure-3PSID", "SAPISID", "APISID", "HSID", "SSID"}
        important = {"CONSENT", "VISITOR_INFO1_LIVE", "YSC"}
        
        return jsonify(
            ok=True,
            total_lines=len(lines),
            youtube_domain_lines=youtube_lines,
            required_found=sorted(list(required & keys_present)),
            important_found=sorted(list(important & keys_present)),
            missing_required=sorted(list(required - keys_present)),
            missing_important=sorted(list(important - keys_present)),
            file_size_bytes=os.path.getsize(path)
        )
    except Exception as e:
        return jsonify(ok=False, reason=f"Cookie dosyası okunamıyor: {str(e)}"), 500

@app.route("/", methods=["GET", "POST"])
def index():
    """Main page - form and download handler"""
    if request.method == "POST":
        # Rate limiting check
        client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', 
                                      request.environ.get('HTTP_X_REAL_IP', 
                                                        request.remote_addr))
        
        if not check_rate_limit(client_ip):
            msg_html = '<div class="msg err">[RATE] Rate limit asildi. 10 dakika icinde maksimum 3 indirme yapabilirsiniz.</div>'
            content = FORM_CONTENT.format(url="", msg_block=msg_html)
            return render_template_string(HTML_SHELL.replace("<!--CONTENT-->", content)), 429

        url = (request.form.get("url") or "").strip()
        
        # Handle cookie file upload
        uploaded_file = request.files.get("cookies")
        if uploaded_file and uploaded_file.filename:
            try:
                # Validate file size
                if len(uploaded_file.read()) > 1024 * 1024:  # 1MB limit for cookie files
                    raise ValueError("Cookie dosyası çok büyük (>1MB)")
                
                uploaded_file.seek(0)  # Reset file pointer
                uploaded_file.save("/tmp/cookies.txt")
                print(f"[cookie] uploaded -> /tmp/cookies.txt (from {client_ip})")
            except Exception as e:
                msg_html = f'<div class="msg err">❌ Cookie dosyası hatası: {str(e)}</div>'
                content = FORM_CONTENT.format(url=url, msg_block=msg_html)
                return render_template_string(HTML_SHELL.replace("<!--CONTENT-->", content)), 400

        try:
            filename = run_download(url)
            return redirect(url_for("done", filename=filename))
        except Exception as e:
            error_msg = str(e)
            # Sanitize error message for display
            if len(error_msg) > 500:
                error_msg = error_msg[:497] + "..."
            
            msg_html = f'<div class="msg err">❌ İndirme Hatası: {error_msg}</div>'
            content = FORM_CONTENT.format(url=url, msg_block=msg_html)
            return render_template_string(HTML_SHELL.replace("<!--CONTENT-->", content)), 400

    # GET: empty form
    content = FORM_CONTENT.format(url="", msg_block="")
    return render_template_string(HTML_SHELL.replace("<!--CONTENT-->", content))

@app.get("/done")
def done():
    """Success page with download button"""
    filename = request.args.get("filename")
    if not filename:
        return redirect(url_for("index"))
    
    # Validate filename exists
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(file_path):
        msg_html = '<div class="msg err">❌ Dosya bulunamadı. Lütfen tekrar indirin.</div>'
        content = FORM_CONTENT.format(url="", msg_block=msg_html)
        return render_template_string(HTML_SHELL.replace("<!--CONTENT-->", content)), 404
    
    # Escape filename for safe JavaScript usage
    safe_filename = filename.replace("'", "\\'").replace('"', '\\"')
    content = DONE_CONTENT.format(filename=safe_filename)
    return HTML_SHELL.replace("<!--CONTENT-->", content)

@app.route("/download/<path:filename>")
def download(filename):
    """File download endpoint with security checks"""
    # Security: prevent directory traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        return "Geçersiz dosya adı", 400
    
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    
    # Check if file exists
    if not os.path.exists(file_path):
        return "Dosya bulunamadı", 404
    
    # Check if it's actually a file (not a directory)
    if not os.path.isfile(file_path):
        return "Geçersiz dosya", 400
    
    try:
        return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)
    except Exception as e:
        print(f"Download error for {filename}: {e}")
        return "Dosya indirilemedi", 500

@app.route("/cleanup", methods=["POST"])
def cleanup():
    """Manual cleanup endpoint (optional)"""
    try:
        files_removed = 0
        current_time = time.time()
        
        for filename in os.listdir(DOWNLOAD_DIR):
            file_path = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.isfile(file_path):
                # Remove files older than 1 hour
                if current_time - os.path.getmtime(file_path) > 3600:
                    os.remove(file_path)
                    files_removed += 1
        
        return jsonify(ok=True, files_removed=files_removed)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

# Background cleanup function
def background_cleanup():
    """Clean old files automatically"""
    import threading
    import atexit
    
    def cleanup_worker():
        while True:
            try:
                current_time = time.time()
                files_cleaned = 0
                
                if os.path.exists(DOWNLOAD_DIR):
                    for filename in os.listdir(DOWNLOAD_DIR):
                        file_path = os.path.join(DOWNLOAD_DIR, filename)
                        if os.path.isfile(file_path):
                            # Remove files older than 2 hours
                            if current_time - os.path.getmtime(file_path) > 7200:
                                try:
                                    os.remove(file_path)
                                    files_cleaned += 1
                                except Exception as e:
                                    print(f"Cleanup error for {filename}: {e}")
                
                if files_cleaned > 0:
                    print(f"[cleanup] Removed {files_cleaned} old files")
                
            except Exception as e:
                print(f"[cleanup] Background cleanup error: {e}")
            
            time.sleep(1800)  # Run every 30 minutes
    
    cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
    cleanup_thread.start()
    
    # Cleanup on exit
    atexit.register(lambda: print("[cleanup] App shutting down"))

# Initialize background cleanup
background_cleanup()

if __name__ == "__main__":
    # Validate environment
    print("[STARTUP] YouTube to MP3 Converter baslatiliyor...")
    print(f"[CONFIG] Download dizini: {DOWNLOAD_DIR}")
    print(f"[CONFIG] FFmpeg mevcut: {ffmpeg_available()}")
    print(f"[CONFIG] Proxy: {'Evet' if PROXY else 'Hayir'}")
    print(f"[CONFIG] Cookies: {'Evet' if ensure_cookiefile() else 'Hayir'}")
    
    # Validate download directory permissions
    try:
        test_file = os.path.join(DOWNLOAD_DIR, "test_write.tmp")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        print("[OK] Yazma izinleri OK")
    except Exception as e:
        print(f"[WARN] Yazma izin problemi: {e}")
    
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "False").lower() == "true"
    
    print(f"[SERVER] Sunucu http://0.0.0.0:{port} adresinde baslatiliyor...")
    
    try:
        app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)
    except KeyboardInterrupt:
        print("\n[EXIT] Uygulama kapatiliyor...")
    except Exception as e:
        print(f"[ERROR] Sunucu hatasi: {e}")