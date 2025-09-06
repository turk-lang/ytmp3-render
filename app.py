import os, re, shutil
from flask import Flask, request, render_template_string, send_from_directory
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# ---- config ----
COOKIE_SRC = "/etc/secrets/cookies.txt"   # Render Secret Files (read-only)
COOKIE_RT  = "/tmp/cookies.txt"           # runtime copy (writable)
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def has_ffmpeg(): return shutil.which("ffmpeg") is not None
YOUTUBE_RE = re.compile(r"(youtu\.be/|youtube\.com/)")
def is_youtube_url(u): return bool(u and YOUTUBE_RE.search(u))

HTML = """
<!doctype html>
<title>🎵 YouTube MP3 İndirici</title>
<h1>🎵 YouTube MP3 İndirici</h1>
<form method="post">
  <input type="text" name="url" value="{{ last_url or '' }}" style="width:70%%">
  <button type="submit">MP3'e Dönüştür</button>
</form>
{% if msg %}<p>{{ msg|safe }}</p>{% endif %}
{% if filename %}<p>✅ <a href="/downloads/{{ filename }}">İndir: {{ filename }}</a></p>{% endif %}
<p style="opacity:.6;font-size:12px;">Not: FFmpeg varsa MP3'e dönüştürülür; yoksa orijinal ses indirilir.</p>
"""

app = Flask(__name__)

@app.route("/downloads/<path:n>")
def dl(n): return send_from_directory(DOWNLOAD_DIR, n, as_attachment=True)

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
        "noplaylist": True, "http_headers": {"User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9"}, "cachedir": False
    }
    if cookie_ok: opts["cookiefile"] = COOKIE_RT
    with YoutubeDL(opts) as y: info = y.extract_info(url, download=False)
    return info, (info or {}).get("formats") or []

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

@app.route("/", methods=["GET","POST"])
def index():
    msg = ""; filename = None; last_url = ""
    if request.method == "POST":
        url = (request.form.get("url") or "").strip(); last_url = url
        if not is_youtube_url(url):
            msg = "❌ Lütfen geçerli bir YouTube video bağlantısı girin."
        else:
            cookie_ok = prep_cookie()
            outtmpl = os.path.join(DOWNLOAD_DIR, "%(title).90s.%(ext)s")
            use_ff = has_ffmpeg()

            base_opts = {
                "outtmpl": outtmpl, "noplaylist": True, "quiet": True,
                "no_warnings": True, "cachedir": False,
                "http_headers": {"User-Agent":"Mozilla/5.0",
                                 "Accept-Language":"en-US,en;q=0.9"},
            }
            if cookie_ok: base_opts["cookiefile"] = COOKIE_RT
            if use_ff:
                base_opts.update({
                    "postprocessors":[{"key":"FFmpegExtractAudio",
                                       "preferredcodec":"mp3",
                                       "preferredquality":"192"}],
                    "postprocessor_args":["-ar","44100"],
                    "prefer_ffmpeg": True,
                })

            # 1) İlk deneme: güçlü zincir
            try:
                op1 = dict(base_opts)
                op1["format"] = "ba/140/251/250/249/171/bestaudio/best"
                with YoutubeDL(op1) as y: info = y.extract_info(url, download=True)
                safe = re.sub(r'[\\/:*?"<>|]', "_", info.get("title","audio"))
                filename = f"{safe}.mp3" if use_ff else f"{safe}.{info.get('ext') or 'm4a'}"
                msg = "✅ Dönüştürme tamam!"
            except DownloadError as e:
                # 2) Olmadı → formatları listele, uygun ID seç, tekrar dene
                print("FIRST TRY FAILED:", e)
                try:
                    info_probe, fmts = list_formats(url, cookie_ok)
                    for f in fmts[:10]:
                        print("FMT", f.get("format_id"), f.get("ext"), f.get("acodec"),
                              f.get("vcodec"), f.get("abr"), f.get("tbr"))
                    chosen = pick_audio(fmts)
                    if not chosen:
                        msg = "❌ Bu video için uygun ses formatı bulunamadı."
                    else:
                        fmt_id = chosen.get("format_id")
                        op2 = dict(base_opts); op2["format"] = str(fmt_id)
                        with YoutubeDL(op2) as y: info = y.extract_info(url, download=True)
                        safe = re.sub(r'[\\/:*?"<>|]', "_", info.get("title","audio"))
                        filename = f"{safe}.mp3" if use_ff else f"{safe}.{info.get('ext') or 'm4a'}"
                        msg = "✅ Dönüştürme tamam!"
                except Exception as e2:
                    import traceback; print("SECOND TRY ERROR:\n", traceback.format_exc())
                    msg = f"❌ Hata: {type(e2).__name__}: {e2}"
            except Exception as e:
                import traceback; print("YT-DLP ERROR:\n", traceback.format_exc())
                msg = f"❌ Hata: {type(e).__name__}: {e}"

    return render_template_string(HTML, msg=msg, filename=filename, last_url=last_url)
