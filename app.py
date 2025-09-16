# -*- coding: utf-8 -*-
"""
Render-friendly Flask app: YouTube → MP3 with yt-dlp

- Cookies: upload via UI (/tmp/cookies.txt) or mount Secret File at /etc/secrets/cookies.txt
- Format seçimi: önce formatları listeler, gerçekten var olan en iyi ses formatını seçer (m4a > webm/opus > diğer)
- Fallback: farklı YouTube client sıralamalarını (web/android/tv/ios) dener
- MP3: FFmpeg varsa mp3'e çevirir, yoksa orijinal ses uzantısını bırakır
- Proxy: YTDLP_PROXY / HTTPS_PROXY / HTTP_PROXY / PROXY ile residential proxy desteği
"""
import os
import shutil
import re
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

# ---------------- HTML ------------------
HTML = r"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>YouTube → MP3</title>
  <style>
    body{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;
         max-width:780px;margin:32px auto;padding:0 16px;line-height:1.5}
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
    Not: FFmpeg varsa MP3'e dönüştürülür; yoksa m4a/webm kalır.<br>
    ⚠ Yalnızca hak sahibi olduğunuz içerikleri indirin. YouTube kullanım şartlarına uyun.
  </div>
</body>
</html>
"""

# ---------------- UTILS -----------------
def ffmpeg_available() -> bool:
    """Check if FFmpeg is available in system PATH"""
    return shutil.which("ffmpeg") is not None

def is_valid_youtube_url(url: str) -> bool:
    """Validate if URL is a valid YouTube URL"""
    youtube_regex = re.compile(
        r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})'
    )
    return bool(youtube_regex.match(url))

def ensure_cookiefile() -> Optional[str]:
    """Find and prepare cookie file"""
    tmp = "/tmp/cookies.txt"
    if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
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
            try:
                shutil.copyfile(src, tmp)
                return tmp
            except Exception as e:
                print(f"Cookie dosyası kopyalama hatası: {e}")
    
    return None

def common_opts(client_order: List[str], cookiefile: Optional[str]) -> Dict[str, Any]:
    """Create common yt-dlp options"""
    opts: Dict[str, Any] = {
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title).90s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "cachedir": False,
        "retries": 3,
        "nocheckcertificate": True,
        "extractor_args": {"youtube": {"player_client": client_order, "skip": ["configs"]}},
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
    """Choose best audio format from available formats"""
    fmts = info.get("formats") or []
    candidates: List[Tuple[float, Dict[str, Any]]] = []
    
    for f in fmts:
        acodec, vcodec = f.get("acodec"), f.get("vcodec")
        if not acodec or acodec == "none": 
            continue
            
        is_audio_only = (vcodec in (None, "none"))
        abr = f.get("abr") or f.get("tbr") or 0
        ext = (f.get("ext") or "").lower()
        
        # Scoring: m4a preferred, audio-only preferred, higher bitrate preferred
        ext_bonus = 20 if ext == "m4a" else (10 if ext == "webm" else 0)
        score = abr + (60 if is_audio_only else 0) + ext_bonus
        candidates.append((score, f))
    
    if not candidates: 
        return "bestaudio/best"
    
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1].get("format_id") or "bestaudio/best"

def clean_filename(filename: str) -> str:
    """Clean filename for safe filesystem usage"""
    # Remove or replace problematic characters
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    filename = re.sub(r'[\x00-\x1f]', '', filename)  # Remove control characters
    return filename.strip()

def run_download(url: str) -> str:
    """Download video and return filename"""
    if not url.strip():
        raise ValueError("URL boş olamaz.")
    
    if not is_valid_youtube_url(url):
        raise ValueError("Geçerli bir YouTube URL'si giriniz.")
    
    cookie = ensure_cookiefile()
    
    # Different client order strategies
    orders = (
        [["web","android","tv"],["android","web","tv"],["ios","android","tv","web"]]
        if cookie else
        [["android","tv","web"],["web","android","tv"],["ios","android","tv","web"]]
    )
    
    last_err = None
    
    for order in orders:
        try:
            # First, extract info without downloading
            opts_probe = common_opts(order, cookie)
            with YoutubeDL(opts_probe) as y1:
                info = y1.extract_info(url, download=False)
                
                if not info:
                    raise DownloadError("Video bilgisi alınamadı.")
                
                if info.get("is_live"):
                    raise DownloadError("Canlı yayın desteklenmiyor.")
                
                # Choose best format
                fmt = choose_format(info)
            
            # Now download with chosen format
            opts_dl = dict(opts_probe)
            opts_dl["format"] = fmt
            
            # Track files before download
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
                return new_files[0]
            
            # Fallback: construct expected filename
            title = clean_filename(info.get('title', 'audio'))
            ext = 'mp3' if ffmpeg_available() else (info.get('ext', 'm4a'))
            expected_filename = f"{title}.{ext}"
            
            # Check if file exists with expected name
            if os.path.exists(os.path.join(DOWNLOAD_DIR, expected_filename)):
                return expected_filename
            
            # If still not found, return the first file that might match
            all_files = os.listdir(DOWNLOAD_DIR) if os.path.exists(DOWNLOAD_DIR) else []
            for f in sorted(all_files, key=lambda x: os.path.getmtime(os.path.join(DOWNLOAD_DIR, x)), reverse=True):
                if title[:20] in f or f.endswith(('.mp3', '.m4a', '.webm', '.opus')):
                    return f
            
            raise DownloadError("Dosya indirme tamamlandı ancak dosya bulunamadı.")
            
        except Exception as e:
            last_err = e
            print(f"İndirme denemesi başarısız ({order}): {e}")
    
    # If all attempts failed
    error_msg = str(last_err) if last_err else "Bilinmeyen hata"
    raise RuntimeError(f"Tüm indirme denemeleri başarısız: {error_msg}")

# ---------------- FLASK -----------------
app = Flask(__name__)

@app.get("/health")
def health():
    """Health check endpoint"""
    return jsonify({
        "ok": True,
        "ffmpeg": ffmpeg_available(),
        "download_dir": DOWNLOAD_DIR,
        "proxy": bool(PROXY)
    })

@app.route("/", methods=["GET", "POST"])
def index():
    """Main route for downloading"""
    msg, filename, url = None, None, ""
    
    if request.method == "POST":
        url = (request.form.get("url") or "").strip()
        file = request.files.get("cookies")
        
        # Handle uploaded cookie file
        if file and file.filename and file.filename.endswith('.txt'):
            try:
                file.save("/tmp/cookies.txt")
                print("Cookie dosyası yüklendi.")
            except Exception as e:
                print(f"Cookie dosyası yükleme hatası: {e}")
        
        # Attempt download
        if url:
            try:
                filename = run_download(url)
                msg = "✅ İndirme tamamlandı."
                print(f"İndirme başarılı: {filename}")
            except Exception as e:
                msg = f"❌ İndirme Hatası: {e}"
                print(f"İndirme hatası: {e}")
        else:
            msg = "❌ URL giriniz."
    
    return render_template_string(HTML, msg=msg, filename=filename, url=url)

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
    print(f"Starting server on port {port}")
    print(f"Download directory: {DOWNLOAD_DIR}")
    print(f"FFmpeg available: {ffmpeg_available()}")
    print(f"Proxy configured: {bool(PROXY)}")
    app.run(host="0.0.0.0", port=port, debug=False)