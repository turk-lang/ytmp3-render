# -*- coding: utf-8 -*-
"""
YouTube → MP3 (Flask) — Safe Boot Build
- Gunicorn altında "Worker failed to boot" yaşanmaması için: yt_dlp eksikse uygulama CRASH etmez,
  formlar ve /health çalışır, indirme denemesinde net hata döner.
- Birleşik (alternative + standard) strateji boru hattı.
"""

import os
import re
import time
import shutil
import random
from typing import Optional, Dict, Any, List, Tuple

from flask import Flask, request, send_from_directory, render_template_string, jsonify, redirect, url_for

# ---- Safe import for yt_dlp ----
YTDLP_AVAILABLE = True
try:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError
except Exception as e:
    YTDLP_AVAILABLE = False
    _YTDLP_IMPORT_ERROR = str(e)

# --------- Config ----------
DOWNLOAD_DIR = os.path.abspath(os.environ.get("DOWNLOAD_DIR", "/var/data"))
try:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
except PermissionError:
    DOWNLOAD_DIR = "/tmp/downloads"
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

PROXY = (
    os.environ.get("YTDLP_PROXY")
    or os.environ.get("HTTPS_PROXY")
    or os.environ.get("HTTP_PROXY")
    or os.environ.get("PROXY")
)

download_sessions: Dict[str, List[float]] = {}

# --------- HTML Shell ---------
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
  </style>
</head>
<body>
  <h2>YouTube → MP3</h2>
  <!--CONTENT-->
  <div class="note">
    <strong>Not:</strong> FFmpeg varsa MP3'e dönüştürülür; yoksa m4a/webm kalır. Yalnızca hak sahibi olduğunuz içerikleri indirin.
    <br><br>
    {yt_note}
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
      btn.disabled = true; btn.textContent = 'İndiriliyor...';
      setTimeout(() => {{ btn.disabled = false; btn.textContent = 'İndir'; }}, 30000);
    }});
  </script>
"""

DONE_CONTENT = r"""
  <div class="msg ok">✅ İndirme tamamlandı.</div>
  <p style="margin-top:12px">
    <a id="dlbtn" class="btn" href="#" onclick="downloadAndRedirect('/download/{filename}', '{filename}')">📥 Dosyayı indir</a>
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
        const a=document.createElement('a'); a.href=url; a.download=filename; a.style.display='none';
        document.body.appendChild(a); a.click(); a.remove();
        let s=3; const el=document.getElementById('countdown');
        const tick=()=>{{ if(s>0){{ el.textContent=`${{s}} sn sonra ana sayfaya dönülecek...`; s--; setTimeout(tick,1000);} else {{ location.href='/'; }} }};
        tick();
      }} catch(e) {{ alert('İndirme sırasında hata oluştu.'); }}
    }}
  </script>
"""

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

# --------- Helpers ---------
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None

def is_valid_youtube_url(url: str) -> bool:
    if not url:
        return False
    if not url.startswith(('http://','https://')):
        url = 'https://' + url
    patterns = [
        r'(?:https?://)?(?:www\.)?(?:youtube|youtu|youtube-nocookie)\.(?:com|be)/'
        r'(?:watch\?v=|embed/|v/|.+\?v=|shorts/)?([A-Za-z0-9_-]{11})(?:\S+)?',
        r'(?:https?://)?(?:m\.)?youtube\.com/shorts/([A-Za-z0-9_-]{11})',
        r'(?:https?://)?youtu\.be/([A-Za-z0-9_-]{11})'
    ]
    return any(re.search(p, url, re.IGNORECASE) for p in patterns)

def check_rate_limit(ip: str) -> bool:
    if not ip:
        return True
    now = time.time()
    sess = download_sessions.setdefault(ip, [])
    sess[:] = [t for t in sess if now - t < 600]  # 10dk
    if len(sess) >= 3:
        return False
    sess.append(now)
    return True

def ensure_cookiefile(refresh: bool = False) -> Optional[str]:
    tmp = "/tmp/cookies.txt"
    if refresh or not (os.path.exists(tmp) and os.path.getsize(tmp) > 0):
        for src in [os.environ.get("YTDLP_COOKIES"),
                    "/etc/secrets/cookies.txt",
                    "/etc/secrets/COOKIES.txt",
                    "/etc/secrets/youtube-cookies.txt",
                    "/app/cookies.txt",
                    "./cookies.txt"]:
            if src and os.path.exists(src) and os.path.getsize(src) > 0:
                try:
                    shutil.copyfile(src, tmp)
                    print(f"[cookie] copied {src} -> {tmp}")
                    return tmp
                except Exception as e:
                    print(f"[cookie] copy failed {src}: {e}")
        print("[cookie] not found")
        return None
    print("[cookie] using existing /tmp/cookies.txt")
    return tmp

def build_opts(*, player_clients, cookiefile: Optional[str] = None, proxy: Optional[str] = PROXY,
               postprocess: bool = True, use_po_token: bool = False, aggressive_bypass: bool = False) -> Dict[str, Any]:
    if isinstance(player_clients, list):
        player_clients = ",".join(player_clients)
    assert isinstance(player_clients, str), "player_clients string olmalı"
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    ]
    selected_ua = random.choice(user_agents)
    opts: Dict[str, Any] = {
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title).90s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "cachedir": False,
        "retries": 6 if aggressive_bypass else 4,
        "fragment_retries": 6 if aggressive_bypass else 4,
        "extractor_retries": 8,
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
        },
        "extractor_args": {
            "youtube": {
                "player_client": player_clients,
                "skip": ["configs"] if not aggressive_bypass else ["configs", "webpage", "js"],
                "player_skip": ["js"] if not aggressive_bypass else ["js", "configs"],
                "comment_sort": "top",
                "max_comments": [0, 0, 0],
            }
        },
        "geo_bypass_country": "US",
        "no_check_formats": True,
        "ignore_no_formats_error": True,
        "ignoreerrors": False,
    }
    if use_po_token:
        po_token = os.environ.get("YTDLP_PO_TOKEN")
        visitor_data = os.environ.get("YTDLP_VISITOR_DATA")
        if po_token and visitor_data:
            opts["extractor_args"]["youtube"]["po_token"] = f"{po_token}:{visitor_data}"
            print("[token] Using PO Token")
    if proxy:
        opts["proxy"] = proxy
        print(f"[proxy] {proxy[:24]}...")
    if cookiefile and os.path.exists(cookiefile):
        opts["cookiefile"] = cookiefile

    force_client = os.environ.get("YTDLP_FORCE_CLIENT")
    if force_client:
        opts["extractor_args"]["youtube"]["player_client"] = force_client
        print(f"[debug] YTDLP_FORCE_CLIENT={force_client}")

    if postprocess and ffmpeg_available() and YTDLP_AVAILABLE:
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
        ext_bonus = 30 if ext == "m4a" else (20 if ext == "webm" else (10 if ext == "mp4" else 0))
        audio_only_bonus = 50 if is_audio_only else 0
        quality_score = min(abr or 0, 320)
        candidates.append((quality_score + audio_only_bonus + ext_bonus, f))
    if not candidates:
        return "bestaudio/best"
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1].get("format_id") or "bestaudio/best"

# --------- Core Download ---------
def run_download(url: str) -> str:
    if not YTDLP_AVAILABLE:
        raise RuntimeError("yt-dlp bulunamadı. Sunucu yöneticisine iletin: pip install -U yt-dlp\nDetay: " + _YTDLP_IMPORT_ERROR)
    if not url:
        raise ValueError("URL boş olamaz.")
    if not is_valid_youtube_url(url):
        raise ValueError("Geçerli bir YouTube URL'si giriniz.")

    cookie = ensure_cookiefile(refresh=False)
    cookie_refreshed = False

    alternative_strategies = [
        ("Emergency TV", ["tv"], False, True, 1, {"extractor_args": {"youtube": {"innertube_host": "youtubei.googleapis.com"}}}),
        ("Web (Bypass)", ["web"], False, True, 2, {"extractor_args": {"youtube": {"skip": ["dash","hls"], "player_skip": ["configs"]}}}),
        ("Android Testsuite", ["android_testsuite"], False, True, 3, {}),
        ("iOS Safari", ["ios"], False, True, 4, {}),
    ]
    standard_strategies = [
        ("Smart TV", ["tv"], False, True, 1, {}),
        ("Android Creator", ["android_creator"], False, True, 2, {}),
        ("Mobile Web", ["mweb"], False, True, 3, {}),
        ("PO + Android", ["android"], True, True, 4, {}),
        ("iOS Music", ["ios_music"], False, True, 5, {}),
        ("TV Embedded", ["tv_embedded"], False, True, 6, {}),
        ("Web Creator", ["web_creator"], False, True, 7, {}),
    ]
    strategies = alternative_strategies + standard_strategies

    last_err: Optional[Exception] = None

    for idx, (name, clients, use_po, aggr, base_delay, extra_opts) in enumerate(strategies, start=1):
        if idx > 1:
            delay = base_delay + (idx * 0.6) + random.uniform(0.4, 1.2)
            time.sleep(delay)
        try:
            # Extract
            opts_info = build_opts(player_clients=clients, cookiefile=cookie, postprocess=False,
                                   use_po_token=use_po, aggressive_bypass=aggr)
            for k,v in extra_opts.items():
                if isinstance(v, dict) and isinstance(opts_info.get(k), dict):
                    opts_info[k].update(v)
                else:
                    opts_info[k] = v

            with YoutubeDL(opts_info) as y1:
                info = y1.extract_info(url, download=False)
                if not info:
                    raise DownloadError("Video metadata extraction failed")
                if info.get("is_live"):
                    raise DownloadError("Live streams are not supported")
                availability = info.get("availability")
                if availability in {"private","premium_only","subscriber_only","needs_auth","unavailable"}:
                    raise DownloadError(f"Video is not accessible: {availability}")
                if info.get("age_limit", 0) > 0 and not cookie:
                    pass
                fmt = choose_format(info)

            # Download
            time.sleep(1.5 + idx*0.2)
            opts_dl = build_opts(player_clients=clients, cookiefile=cookie, postprocess=True,
                                 use_po_token=use_po, aggressive_bypass=aggr)
            for k,v in extra_opts.items():
                if isinstance(v, dict) and isinstance(opts_dl.get(k), dict):
                    opts_dl[k].update(v)
                else:
                    opts_dl[k] = v
            opts_dl["format"] = fmt

            files_before = set(os.listdir(DOWNLOAD_DIR)) if os.path.exists(DOWNLOAD_DIR) else set()
            with YoutubeDL(opts_dl) as y2:
                y2.download([url])
            files_after = set(os.listdir(DOWNLOAD_DIR)) if os.path.exists(DOWNLOAD_DIR) else set()
            new_files = sorted(list(files_after - files_before),
                               key=lambda f: os.path.getmtime(os.path.join(DOWNLOAD_DIR, f)),
                               reverse=True)
            if new_files:
                return new_files[0]

            # Fallback name
            title = "".join(c for c in (info.get("title") or "audio") if c.isalnum() or c in " ._-")[:50].strip()
            ext = "mp3" if ffmpeg_available() else (info.get("ext") or "m4a")
            return f"{title}.{ext}"

        except Exception as e:
            last_err = e
            err = str(e).lower()
            if "failed to extract any player response" in err:
                if not cookie_refreshed and idx <= 5:
                    cookie = ensure_cookiefile(refresh=True)
                    cookie_refreshed = True
                    time.sleep(8 + idx * 2)
            elif "sign in to confirm" in err or "bot" in err:
                if not cookie_refreshed and idx <= 4:
                    cookie = ensure_cookiefile(refresh=True)
                    cookie_refreshed = True
                    time.sleep(6 + random.uniform(1,3))
            elif any(k in err for k in ["private","unavailable","removed","deleted"]):
                break
            elif any(k in err for k in ["rate","limit","quota","too many requests"]):
                time.sleep(15 + idx*3 + random.uniform(5,10))
            elif any(k in err for k in ["network","timeout","connection","resolve"]):
                time.sleep(3 + random.uniform(1,2))
            continue

    # Emergency (multi-client + PO)
    if YTDLP_AVAILABLE:
        try:
            cookie2 = ensure_cookiefile(refresh=True) or cookie
            opts_e = build_opts(player_clients=["tv","android","mweb"], cookiefile=cookie2, postprocess=False,
                                use_po_token=True, aggressive_bypass=True)
            with YoutubeDL(opts_e) as fx:
                info = fx.extract_info(url, download=False)
                fmt = choose_format(info) if info else "bestaudio/best"
            time.sleep(2.0)
            opts_edl = build_opts(player_clients=["tv","android","mweb"], cookiefile=cookie2, postprocess=True,
                                  use_po_token=True, aggressive_bypass=True)
            opts_edl["format"] = fmt
            before = set(os.listdir(DOWNLOAD_DIR)) if os.path.exists(DOWNLOAD_DIR) else set()
            with YoutubeDL(opts_edl) as fy:
                fy.download([url])
            after = set(os.listdir(DOWNLOAD_DIR)) if os.path.exists(DOWNLOAD_DIR) else set()
            news = sorted(list(after - before), key=lambda f: os.path.getmtime(os.path.join(DOWNLOAD_DIR, f)), reverse=True)
            if news:
                return news[0]
        except Exception:
            pass

    msg = str(last_err) if last_err else ("yt-dlp eksik" if not YTDLP_AVAILABLE else "Bilinmeyen hata")
    low = msg.lower()
    if not YTDLP_AVAILABLE:
        hint = "\n\n[SETUP] Sunucuda yt-dlp kurulu değil: pip install -U yt-dlp"
    elif "failed to extract any player response" in low:
        hint = ("\n\n[FIX] PLAYER RESPONSE ERROR:"
                "\n• yt-dlp güncelle"
                "\n• Chrome'dan cookies.txt export edip yükle"
                "\n• Residential proxy (YTDLP_PROXY)"
                "\n• PO token (YTDLP_PO_TOKEN + YTDLP_VISITOR_DATA)")
    elif "bot" in low or "sign in to confirm" in low:
        hint = "\n\n[BOT] Doğrulama gerekiyor: taze cookies.txt + residential proxy"
    elif any(k in low for k in ["private","unavailable"]):
        hint = "\n\n[UNAVAIL] Video özel/kaldırılmış/geo-engelli olabilir."
    elif any(k in low for k in ["rate","limit","quota"]):
        hint = "\n\n[RATE] Limit aşıldı. 30-60 dk sonra tekrar deneyin."
    else:
        hint = "\n\n[GENERAL] URL/çerez/proxy ayarlarını kontrol edin; tekrar deneyin."
    raise RuntimeError(f"Tüm bypass stratejileri başarısız. Son hata: {msg}{hint}")

# --------- Flask Routes ---------
@app.errorhandler(413)
def too_large(e):
    return jsonify(error="Dosya çok büyük. Maksimum 16MB."), 413

@app.errorhandler(500)
def internal_error(e):
    return jsonify(error="Sunucu hatası. Lütfen tekrar deneyin."), 500

@app.get("/health")
def health():
    return jsonify(
        ok=True,
        yt_dlp=YTDLP_AVAILABLE,
        yt_dlp_error=(None if YTDLP_AVAILABLE else _YTDLP_IMPORT_ERROR),
        ffmpeg=ffmpeg_available(),
        download_dir=DOWNLOAD_DIR,
        proxy=bool(PROXY),
        disk_free_gb=(shutil.disk_usage(DOWNLOAD_DIR).free // (1024**3)) if os.path.exists(DOWNLOAD_DIR) else 0
    )

@app.get("/cookie_check")
def cookie_check():
    path = "/tmp/cookies.txt"
    if not os.path.exists(path):
        for sp in ["/etc/secrets/cookies.txt","/etc/secrets/COOKIES.txt","/etc/secrets/youtube-cookies.txt","/app/cookies.txt","./cookies.txt"]:
            if os.path.exists(sp):
                try: shutil.copyfile(sp, path); break
                except Exception as e: print(f"[cookie] copy failed {sp}: {e}")
    if not os.path.exists(path):
        return jsonify(ok=False, reason="cookies.txt yok"), 404
    try:
        lines = []
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    lines.append(line)
        keys_present = set(); youtube_lines = 0
        for line in lines:
            parts = line.split('\t')
            if len(parts) >= 7:
                domain = parts[0]; cname = parts[5]
                if "youtube.com" in domain or ".youtube." in domain:
                    youtube_lines += 1; keys_present.add(cname)
        required = {"SID","__Secure-3PSID","SAPISID","APISID","HSID","SSID"}
        important = {"CONSENT","VISITOR_INFO1_LIVE","YSC"}
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
        return jsonify(ok=False, reason=f"Cookie dosyası okunamıyor: {e}"), 500

@app.route("/", methods=["GET","POST"])
def index():
    yt_note = ""
    if not YTDLP_AVAILABLE:
        yt_note = "<div class='msg err'>⚠️ Sunucuda yt-dlp kurulu değil. Yöneticiye iletin: <code>pip install -U yt-dlp</code></div>"
    content_shell = HTML_SHELL.format(yt_note=yt_note)

    if request.method == "POST":
        client_ip = request.environ.get('HTTP_X_FORWARDED_FOR',
                        request.environ.get('HTTP_X_REAL_IP', request.remote_addr))
        if not check_rate_limit(client_ip):
            msg_html = '<div class="msg err">⏳ Rate limit aşıldı. 10 dakika içinde en fazla 3 indirme.</div>'
            content = FORM_CONTENT.format(url="", msg_block=msg_html)
            return render_template_string(content_shell.replace("<!--CONTENT-->", content)), 429

        url = (request.form.get("url") or "").strip()

        uploaded = request.files.get("cookies")
        if uploaded and uploaded.filename:
            try:
                data = uploaded.read()
                if len(data) > 1024*1024:
                    raise ValueError("Cookie dosyası 1MB'den büyük")
                with open("/tmp/cookies.txt","wb") as f: f.write(data)
            except Exception as e:
                msg_html = f'<div class="msg err">❌ Cookie hatası: {e}</div>'
                content = FORM_CONTENT.format(url=url, msg_block=msg_html)
                return render_template_string(content_shell.replace("<!--CONTENT-->", content)), 400

        try:
            filename = run_download(url)
            return redirect(url_for("done", filename=filename))
        except Exception as e:
            msg = str(e)
            if len(msg) > 500: msg = msg[:497] + "..."
            msg_html = f'<div class="msg err">❌ İndirme Hatası: {msg}</div>'
            content = FORM_CONTENT.format(url=url, msg_block=msg_html)
            return render_template_string(content_shell.replace("<!--CONTENT-->", content)), 400

    content = FORM_CONTENT.format(url="", msg_block="")
    return render_template_string(HTML_SHELL.format(yt_note="")\
                                  .replace("<!--CONTENT-->", content))

@app.get("/done")
def done():
    filename = request.args.get("filename")
    if not filename:
        return redirect(url_for("index"))
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(file_path):
        msg_html = '<div class="msg err">❌ Dosya bulunamadı. Lütfen tekrar indirin.</div>'
        content = FORM_CONTENT.format(url="", msg_block=msg_html)
        return render_template_string(HTML_SHELL.format(yt_note="").replace("<!--CONTENT-->", content)), 404
    safe_filename = filename.replace("'", "\\'").replace('"','\\"')
    content = DONE_CONTENT.format(filename=safe_filename)
    return HTML_SHELL.format(yt_note="").replace("<!--CONTENT-->", content)

@app.route("/download/<path:filename>")
def download(filename):
    if ".." in filename or "/" in filename or "\\" in filename:
        return "Geçersiz dosya adı", 400
    fp = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(fp): return "Dosya bulunamadı", 404
    if not os.path.isfile(fp): return "Geçersiz dosya", 400
    try:
        return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)
    except Exception as e:
        return "Dosya indirilemedi", 500

# Force route (manuel client test)
def run_download_with_clients(url: str, clients: List[str], *, use_po_token: bool = False, aggressive_bypass: bool = True) -> str:
    if not YTDLP_AVAILABLE:
        raise RuntimeError("yt-dlp eksik. 'pip install -U yt-dlp'")
    if not is_valid_youtube_url(url):
        raise ValueError("Geçerli bir YouTube URL'si giriniz.")
    cookie = ensure_cookiefile(refresh=False)
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError
    opts_info = build_opts(player_clients=clients, cookiefile=cookie, postprocess=False,
                           use_po_token=use_po_token, aggressive_bypass=aggressive_bypass)
    with YoutubeDL(opts_info) as y1:
        info = y1.extract_info(url, download=False)
        if not info: raise DownloadError("Video metadata extraction failed")
        if info.get("is_live"): raise DownloadError("Live streams are not supported")
        fmt = choose_format(info)
    time.sleep(1.5)
    opts_dl = build_opts(player_clients=clients, cookiefile=cookie, postprocess=True,
                         use_po_token=use_po_token, aggressive_bypass=aggressive_bypass)
    opts_dl["format"] = fmt
    before = set(os.listdir(DOWNLOAD_DIR)) if os.path.exists(DOWNLOAD_DIR) else set()
    with YoutubeDL(opts_dl) as y2:
        y2.download([url])
    after = set(os.listdir(DOWNLOAD_DIR)) if os.path.exists(DOWNLOAD_DIR) else set()
    news = sorted(list(after - before), key=lambda f: os.path.getmtime(os.path.join(DOWNLOAD_DIR, f)), reverse=True)
    if news: return news[0]
    title = "".join(c for c in (info.get("title") or "audio") if c.isalnum() or c in " ._-")[:50].strip()
    ext = "mp3" if ffmpeg_available() else (info.get("ext") or "m4a")
    return f"{title}.{ext}"

@app.get("/force")
def force():
    url = (request.args.get("url") or "").strip()
    if not url:
        return jsonify(ok=False, error="url parametresi gerekli"), 400
    clients = [c.strip() for c in (request.args.get("clients","tv").split(",")) if c.strip()]
    use_po = (request.args.get("po","0").lower() in ("1","true","yes","on"))
    aggr = (request.args.get("aggr","1").lower() in ("1","true","yes","on"))
    try:
        filename = run_download_with_clients(url, clients, use_po_token=use_po, aggressive_bypass=aggr)
        return redirect(url_for("done", filename=filename))
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 400

# Background cleanup
def background_cleanup():
    import threading, atexit
    def worker():
        while True:
            try:
                now = time.time(); cleaned = 0
                if os.path.exists(DOWNLOAD_DIR):
                    for fn in os.listdir(DOWNLOAD_DIR):
                        fp = os.path.join(DOWNLOAD_DIR, fn)
                        if os.path.isfile(fp) and now - os.path.getmtime(fp) > 7200:
                            try: os.remove(fp); cleaned += 1
                            except Exception: pass
                time.sleep(1800)
            except Exception:
                time.sleep(1800)
    threading.Thread(target=worker, daemon=True).start()
    atexit.register(lambda: None)
background_cleanup()

if __name__ == "__main__":
    print("[START] Safe Boot — Flask app")
    print(f"[CFG] yt-dlp: {YTDLP_AVAILABLE}")
    if not YTDLP_AVAILABLE:
        print(f"[CFG] yt-dlp import error: {_YTDLP_IMPORT_ERROR}")
    print(f"[CFG] Download dir: {DOWNLOAD_DIR}")
    print(f"[CFG] FFmpeg: {ffmpeg_available()}")
    print(f"[CFG] Proxy: {'Evet' if PROXY else 'Hayır'}")
    try:
        test_file = os.path.join(DOWNLOAD_DIR, "test_write.tmp")
        with open(test_file,"w") as f: f.write("ok")
        os.remove(test_file)
    except Exception as e:
        print(f"[WARN] Yazma izni problemi: {e}")
    port = int(os.environ.get("PORT","5000"))
    debug = os.environ.get("FLASK_DEBUG","False").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)
