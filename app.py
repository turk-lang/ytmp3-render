import os
import re
import shutil
from flask import Flask, request, render_template_string, send_file
from yt_dlp import YoutubeDL

app = Flask(__name__)

# İndirilen dosyalar buraya kaydedilecek
DOWNLOAD_DIR = "/app/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Basit HTML arayüz
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="UTF-8">
  <title>YouTube MP3 İndirici</title>
</head>
<body>
  <h2>🎵 YouTube MP3 İndirici</h2>
  <form method="POST">
    <input type="text" name="url" size="60" placeholder="YouTube linkini buraya yapıştır" required>
    <button type="submit">MP3'e Dönüştür</button>
  </form>
  {% if error %}
    <p style="color:red;">❌ Hata: {{ error }}</p>
  {% endif %}
  {% if msg %}
    <p style="color:green;">{{ msg }}</p>
  {% endif %}
  {% if filename %}
    <p><a href="/download/{{ filename }}">⬇️ İndir: {{ filename }}</a></p>
  {% endif %}
  <p><small>Not: FFmpeg varsa MP3'e dönüştürülür; yoksa orijinal ses indirilir.</small></p>
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def index():
    error = None
    msg = None
    filename = None

    if request.method == "POST":
        url = request.form.get("url")
        if not url:
            error = "Lütfen geçerli bir YouTube linki giriniz."
        else:
            try:
                use_ff = shutil.which("ffmpeg") is not None
                outtmpl = os.path.join(DOWNLOAD_DIR, "%(title).90s.%(ext)s")

                base_opts = {
                    "outtmpl": outtmpl,
                    "noplaylist": True,
                    "quiet": True,
                    "no_warnings": True,
                    "cachedir": False,
                    "http_headers": {
                        "User-Agent": "Mozilla/5.0",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                }

                # Cookie dosyası varsa ekle
                if os.path.exists("/tmp/cookies.txt"):
                    base_opts["cookiefile"] = "/tmp/cookies.txt"

                if use_ff:
                    base_opts.update({
                        "postprocessors": [{
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192",
                        }],
                        "postprocessor_args": ["-ar", "44100"],
                        "prefer_ffmpeg": True,
                    })

                def run_dl(fmt):
                    opts = dict(base_opts)
                    if fmt:
                        opts["format"] = fmt
                    with YoutubeDL(opts) as y:
                        return y.extract_info(url, download=True)

                try:
                    # İlk deneme: m4a + webm/opus ID’leri + bestaudio
                    info = run_dl("ba/140/251/250/249/171/bestaudio/best")
                except Exception as e1:
                    print("FIRST TRY FAILED:", e1)
                    try:
                        # İkinci deneme: formatları listele ve en iyi sesi seç
                        probe_opts = dict(base_opts)
                        probe_opts["skip_download"] = True
                        with YoutubeDL(probe_opts) as y:
                            meta = y.extract_info(url, download=False)

                        formats = (meta or {}).get("formats") or []
                        candidates = []
                        for f in formats:
                            if f.get("vcodec") == "none" or (f.get("acodec") not in (None, "none")):
                                candidates.append(f)

                        def score(f):
                            ext = (f.get("ext") or "").lower()
                            pref = 2 if ext == "m4a" else (1 if ext in ("webm", "opus") else 0)
                            abr = f.get("abr") or f.get("tbr") or 0
                            return (pref, float(abr))

                        chosen = max(candidates, key=score) if candidates else None
                        if not chosen:
                            raise RuntimeError("Ses formatı bulunamadı.")
                        info = run_dl(str(chosen.get("format_id")))
                    except Exception as e2:
                        import traceback
                        print("SECOND TRY ERROR:\n", traceback.format_exc())
                        raise

                # Dosya adını güvenli hale getir
                safe = re.sub(r'[\\/:*?"<>|]', "_", info.get("title", "audio"))
                filename = f"{safe}.mp3" if use_ff else f"{safe}.{info.get('ext') or 'm4a'}"
                msg = "✅ Dönüştürme tamam!"

            except Exception as e:
                error = f"DownloadError: {str(e)}"

    return render_template_string(HTML_TEMPLATE, error=error, msg=msg, filename=filename)

@app.route("/download/<path:filename>")
def download_file(filename):
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(file_path):
        return "Dosya bulunamadı", 404
    return send_file(file_path, as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
