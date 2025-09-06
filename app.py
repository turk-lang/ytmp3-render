import os, re, shutil
from flask import Flask, request, render_template_string, send_from_directory
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# ---- config ----
COOKIE_SRC = "/etc/secrets/cookies.txt"   # Secret Files (read-only)
COOKIE_RT  = "/tmp/cookies.txt"           # runtime copy
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def has_ffmpeg(): return shutil.which("ffmpeg") is not None
YOUTUBE_RE = re.compile(r"(youtu\.be/|youtube\.com/)")
def is_youtube_url(u): return bool(u and YOUTUBE_RE.search(u))

HTML = """
<!doctype html>
<html>
<head>
    <title>🎵 YouTube MP3 İndirici</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }
        input[type="text"] { padding: 10px; font-size: 16px; border: 2px solid #ddd; border-radius: 5px; }
        button { padding: 10px 20px; font-size: 16px; background: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer; }
        button:hover { background: #0056b3; }
        .success { color: #28a745; font-weight: bold; }
        .error { color: #dc3545; font-weight: bold; }
        .download-link { display: inline-block; padding: 10px 15px; background: #28a745; color: white; text-decoration: none; border-radius: 5px; margin-top: 10px; }
        .download-link:hover { background: #1e7e34; }
    </style>
</head>
<body>
    <h1>🎵 YouTube MP3 İndirici</h1>
    <form method="post">
        <input type="text" name="url" value="{{ last_url or '' }}" placeholder="YouTube video URL'sini buraya yapıştırın..." style="width:70%">
        <button type="submit">MP3'e Dönüştür</button>
    </form>
    
    {% if msg %}
        <p class="{% if 'error' in msg.lower() or '❌' in msg %}error{% else %}success{% endif %}">{{ msg|safe }}</p>
    {% endif %}
    
    {% if filename %}
        <p>✅ <a href="/downloads/{{ filename }}" class="download-link">📥 İndir: {{ filename }}</a></p>
    {% endif %}
    
    <div style="margin-top: 30px; padding: 15px; background: #f8f9fa; border-radius: 5px; font-size: 14px; color: #666;">
        <p><strong>ℹ️ Bilgi:</strong></p>
        <ul>
            <li>FFmpeg varsa MP3'e dönüştürülür; yoksa orijinal ses formatı indirilir</li>
            <li>Sadece kişisel kullanım için tasarlanmıştır</li>
            <li>Telif hakkı korumalı içerikleri indirmekten kaçının</li>
        </ul>
    </div>
</body>
</html>
"""

app = Flask(__name__)

@app.route("/downloads/<path:n>")
def dl(n): 
    return send_from_directory(DOWNLOAD_DIR, n, as_attachment=True)

def prep_cookie():
    ok = False
    try:
        if os.path.exists(COOKIE_SRC):
            with open(COOKIE_SRC, "rb") as s, open(COOKIE_RT, "wb") as d:
                d.write(s.read())
            os.chmod(COOKIE_RT, 0o600)
            ok = os.path.getsize(COOKIE_RT) > 0
    except Exception as e:
        print("COOKIE PREP ERROR:", e)
    print("COOKIE_FOUND=", ok)
    return ok

def list_formats(url, cookie_ok):
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "noplaylist": True, "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9"}, "cachedir": False
    }
    if cookie_ok: 
        opts["cookiefile"] = COOKIE_RT
    
    try:
        with YoutubeDL(opts) as y: 
            info = y.extract_info(url, download=False)
        return info, (info or {}).get("formats") or []
    except Exception as e:
        print(f"Format listing error: {e}")
        return None, []

def pick_audio(formats):
    # En iyi ses: m4a > webm/opus > diğer, sonra en yüksek abr/tbr
    cands = []
    for f in formats:
        v, a = f.get("vcodec"), f.get("acodec")
        if v == "none" or (a not in (None, "none")):
            cands.append(f)
    
    def score(f):
        ext = (f.get("ext") or "").lower()
        pref = 2 if ext == "m4a" else (1 if ext in ("webm","opus") else 0)
        abr = f.get("abr") or f.get("tbr") or 0
        return (pref, float(abr))
    
    return (max(cands, key=score) if cands else None)

def clean_filename(title):
    """Dosya adını temizle"""
    if not title:
        return "audio"
    # Türkçe karakterleri koru, sadece zararlı karakterleri değiştir
    safe = re.sub(r'[\\/:*?"<>|]', "_", title)
    safe = re.sub(r'[^\w\s\-_\.]', "_", safe)  # Özel karakterleri alt çizgi yap
    return safe[:90]  # Maksimum 90 karakter

@app.route("/", methods=["GET","POST"])
def index():
    msg = ""
    filename = None
    last_url = ""
    
    if request.method == "POST":
        url = (request.form.get("url") or "").strip()
        last_url = url
        
        if not url:
            msg = "❌ Lütfen bir YouTube video bağlantısı girin."
        elif not is_youtube_url(url):
            msg = "❌ Lütfen geçerli bir YouTube video bağlantısı girin."
        else:
            try:
                cookie_ok = prep_cookie()
                use_ff = has_ffmpeg()
                
                print(f"FFmpeg available: {use_ff}")
                print(f"Cookie available: {cookie_ok}")
                
                outtmpl = os.path.join(DOWNLOAD_DIR, "%(title).90s.%(ext)s")
                
                base_opts = {
                    "outtmpl": outtmpl, 
                    "noplaylist": True, 
                    "quiet": True,
                    "no_warnings": True, 
                    "cachedir": False,
                    "http_headers": {
                        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept-Language":"en-US,en;q=0.9"
                    },
                    "socket_timeout": 30,
                    "retries": 3,
                }
                
                if cookie_ok: 
                    base_opts["cookiefile"] = COOKIE_RT
                    
                if use_ff:
                    base_opts.update({
                        "postprocessors":[{
                            "key":"FFmpegExtractAudio",
                            "preferredcodec":"mp3",
                            "preferredquality":"192"
                        }],
                        "postprocessor_args":["-ar","44100"],
                        "prefer_ffmpeg": True,
                    })

                # 1) İlk deneme: basit format seçimi
                success = False
                try:
                    op1 = dict(base_opts)
                    # Daha güvenli format string'i
                    op1["format"] = "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best"
                    
                    with YoutubeDL(op1) as y: 
                        info = y.extract_info(url, download=True)
                    
                    title = info.get("title", "audio")
                    safe_title = clean_filename(title)
                    ext = "mp3" if use_ff else (info.get('ext') or 'm4a')
                    filename = f"{safe_title}.{ext}"
                    
                    msg = f"✅ '{title}' başarıyla {'MP3' if use_ff else ext.upper()}'e dönüştürüldü!"
                    success = True
                    
                except (DownloadError, Exception) as e:
                    print("FIRST TRY FAILED:", str(e))
                    # 2) İkinci deneme: format ID ile
                    try:
                        # Önce info alıp format seç
                        info_opts = dict(base_opts)
                        info_opts["skip_download"] = True
                        
                        with YoutubeDL(info_opts) as y:
                            info_probe = y.extract_info(url, download=False)
                        
                        if not info_probe:
                            msg = "❌ Video bilgisi alınamadı. Video özel veya yaş sınırlı olabilir."
                        else:
                            formats = info_probe.get("formats") or []
                            if not formats:
                                msg = "❌ Bu video için format bilgisi bulunamadı."
                            else:
                                # En iyi ses formatını seç
                                audio_formats = []
                                for f in formats:
                                    acodec = f.get("acodec")
                                    vcodec = f.get("vcodec") 
                                    if acodec and acodec != "none" and (not vcodec or vcodec == "none"):
                                        audio_formats.append(f)
                                
                                if not audio_formats:
                                    # Ses formatı bulamazsa herhangi bir format dene
                                    chosen_format = formats[0] if formats else None
                                else:
                                    # En yüksek bitrate'li ses formatını seç
                                    chosen_format = max(audio_formats, key=lambda x: x.get("abr", 0) or x.get("tbr", 0) or 0)
                                
                                if chosen_format:
                                    format_id = chosen_format.get("format_id")
                                    op2 = dict(base_opts)
                                    op2["format"] = format_id
                                    
                                    with YoutubeDL(op2) as y: 
                                        info = y.extract_info(url, download=True)
                                    
                                    title = info.get("title", "audio")
                                    safe_title = clean_filename(title)
                                    ext = "mp3" if use_ff else (info.get('ext') or 'm4a')
                                    filename = f"{safe_title}.{ext}"
                                    
                                    msg = f"✅ '{title}' başarıyla {'MP3' if use_ff else ext.upper()}'e dönüştürüldü!"
                                    success = True
                                else:
                                    msg = "❌ Uygun format bulunamadı."
                    
                    except Exception as e2:
                        import traceback
                        print("SECOND TRY ERROR:\n", traceback.format_exc())
                        error_msg = str(e2)
                        if "Sign in to confirm your age" in error_msg:
                            msg = "❌ Bu video yaş sınırlaması nedeniyle indirilemez."
                        elif "Private video" in error_msg:
                            msg = "❌ Bu video özel olarak ayarlanmış ve indirilemez."
                        elif "Video unavailable" in error_msg:
                            msg = "❌ Video mevcut değil veya kaldırılmış."
                        else:
                            msg = f"❌ İndirme hatası: Video erişilemez durumda."
                
                except Exception as e:
                    import traceback
                    print("YT-DLP ERROR:\n", traceback.format_exc())
                    msg = f"❌ Genel hata: {str(e)}"
            
            except Exception as e:
                print(f"Unexpected error: {e}")
                msg = f"❌ Beklenmeyen bir hata oluştu: {str(e)}"

    return render_template_string(HTML, msg=msg, filename=filename, last_url=last_url)

# Render deployment için gerekli
if __name__ == "__main__":
    # Render otomatik olarak PORT environment variable'ını set eder
    port = int(os.environ.get("PORT", 5000))
    # Production'da debug=False olmalı
    debug = os.environ.get("FLASK_ENV") == "development"
    
    print(f"Starting Flask app on port {port}")
    print(f"Debug mode: {debug}")
    print(f"FFmpeg available: {has_ffmpeg()}")
    
    app.run(host="0.0.0.0", port=port, debug=debug)