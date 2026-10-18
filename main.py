import os
import sys
import json
import subprocess
import requests
import pandas as pd
import yt_dlp

# ==========================================
# CONFIGURACIÓ PRINCIPAL
# ==========================================
MODE_PROVA = True  # True = envia MP4 a Telegram. False = Zernio + text Telegram

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ZERNIO_API_KEY = os.getenv("ZERNIO_API_KEY")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "rjydly/carpetcleaning")

HISTORY_FILE = "history.json"
ACCOUNTS_FILE = "accounts.csv"
TMP_DIR = "temp_media"

os.makedirs(TMP_DIR, exist_ok=True)

if not os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "w") as f:
        json.dump([], f)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}

# ==========================================
# UTILITATS TELEGRAM
# ==========================================
def send_telegram_msg(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Variables de Telegram no definides.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print(f"Avís Telegram text: {e}")

def send_telegram_video(video_path, caption=""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Variables de Telegram no definides.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"
    try:
        with open(video_path, "rb") as f:
            files = {"video": f}
            data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption}
            print(f"📤 Enviant {video_path} a Telegram...")
            res = requests.post(url, data=data, files=files, timeout=240)
            if res.status_code == 200:
                print("✅ Vídeo enviat a Telegram correctament!")
            else:
                print(f"Avís Telegram vídeo ({res.status_code}): {res.text}")
    except Exception as e:
        print(f"Avís enviant fitxer a Telegram: {e}")

# ==========================================
# CONTROL DE DUPLICATS
# ==========================================
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

# ==========================================
# EXTRACCIÓ DE VÍDEOS (TIKWM + YT-DLP FALLBACK)
# ==========================================
def fetch_tikwm(handle, history):
    candidates = []
    # TikWM funciona millor amb POST i l'arrova inclosa
    url = "https://www.tikwm.com/api/user/posts"
    payload = {"unique_id": handle, "count": 15, "cursor": 0}

    try:
        res = requests.post(url, data=payload, headers=HEADERS, timeout=15)
        if res.status_code == 200:
            data = res.json()
            if data.get("code") == 0 and "data" in data and "videos" in data["data"]:
                for v in data["data"]["videos"]:
                    vid_id = str(v.get("id"))
                    duration = v.get("duration", 0)
                    if vid_id in history or duration > 600 or duration == 0:
                        continue
                    candidates.append({
                        "id": vid_id,
                        "account": handle,
                        "title": v.get("title", "Satisfying Carpet Cleaning"),
                        "views": v.get("play_count", 0),
                        "duration": duration,
                        "download_url": v.get("play"),
                        "engine": "tikwm"
                    })
    except Exception as e:
        print(f"Avís TikWM per {handle}: {e}")

    return candidates

def fetch_ytdlp(handle, history):
    candidates = []
    clean_name = handle.replace("@", "")
    profile_url = f"https://www.tiktok.com/@{clean_name}"
    ydl_opts = {
        'extract_flat': True,
        'playlist_end': 10,
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(profile_url, download=False)
            if 'entries' in info and info['entries']:
                for entry in info['entries']:
                    if not entry:
                        continue
                    vid_id = str(entry.get('id'))
                    duration = entry.get('duration') or 0
                    if vid_id in history or duration > 600:
                        continue
                    candidates.append({
                        "id": vid_id,
                        "account": handle,
                        "title": entry.get("title") or "Satisfying Carpet Cleaning",
                        "views": entry.get("view_count") or 0,
                        "duration": duration,
                        "download_url": entry.get("url") or f"https://www.tiktok.com/@{clean_name}/video/{vid_id}",
                        "engine": "ytdlp"
                    })
    except Exception as e:
        print(f"Avís yt-dlp per {handle}: {e}")

    return candidates

def get_best_video():
    if not os.path.exists(ACCOUNTS_FILE):
        print(f"❌ No s'ha trobat {ACCOUNTS_FILE}")
        return None

    df = pd.read_csv(ACCOUNTS_FILE)
    accounts = df["username"].dropna().tolist()
    history = load_history()

    all_candidates = []

    for acc in accounts:
        handle = acc.strip()
        if not handle.startswith("@"):
            handle = f"@{handle}"

        print(f"🔍 Cercant vídeos a: {handle}...")
        
        # 1. Intentem TikWM
        found = fetch_tikwm(handle, history)
        
        # 2. Si falla TikWM, intentem yt-dlp de suport
        if not found:
            print(f"🔄 TikWM buit/rebutjat. Provant motor alternatiu (yt-dlp)...")
            found = fetch_ytdlp(handle, history)

        all_candidates.extend(found)

    if not all_candidates:
        print("ℹ️ No hi ha vídeos nous per descarregar.")
        return None

    # Ordenem pel que tingui més visualitzacions
    all_candidates.sort(key=lambda x: x["views"], reverse=True)
    return all_candidates[0]

# ==========================================
# PROCESSAMENT FFMPEG
# ==========================================
def get_exact_duration(file_path):
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", file_path
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(res.stdout.strip())
    except Exception:
        return 0

def process_video(raw_path, approx_duration):
    exact_duration = get_exact_duration(raw_path)
    duration = exact_duration if exact_duration > 0 else approx_duration
    print(f"⏱️ Durada real del vídeo: {duration:.2f} segons")

    output_files = []
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    # CAS A: Entre 1m 29s (89s) i 2 minuts (120s) -> accelerar per encabir-lo en 88s
    if 89 < duration <= 120:
        print(f"⚡ Accelerant vídeo de {duration:.1f}s a 88s...")
        out_path = os.path.join(TMP_DIR, "output_speed.mp4")
        speed_factor = duration / 88.0
        pts_factor = 1.0 / speed_factor

        cmd = [
            "ffmpeg", "-y", "-i", raw_path,
            "-filter_complex",
            f"[0:v]setpts={pts_factor:.4f}*PTS,scale=960:1700:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=white[v]",
            "-map", "[v]", "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
            out_path
        ]
        subprocess.run(cmd, check=True)
        output_files.append((out_path, ""))

    # CAS B: Més de 2 minuts -> dividir en trossos d'1m 20s (80s)
    elif duration > 120:
        chunk_len = 80
        total_parts = int((duration // chunk_len) + (1 if duration % chunk_len > 0 else 0))
        print(f"✂️ Vídeo de {duration:.1f}s dividit en {total_parts} parts d'1m 20s...")

        for idx in range(total_parts):
            start = idx * chunk_len
            part_num = idx + 1
            part_text = f"Part {part_num}"
            out_path = os.path.join(TMP_DIR, f"output_part_{part_num}.mp4")

            vf = (
                f"scale=960:1700:force_original_aspect_ratio=decrease,"
                f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=white,"
                f"drawtext=fontfile={font_path}:text='{part_text}':fontcolor=black:fontsize=65:x=(w-text_w)/2:y=45"
            )

            cmd = [
                "ffmpeg", "-y", "-ss", str(start), "-i", raw_path,
                "-t", str(chunk_len),
                "-vf", vf, "-an",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
                out_path
            ]
            subprocess.run(cmd, check=True)
            output_files.append((out_path, part_text))

    # CAS C: Vídeo curt (< 89s)
    else:
        print("📐 Processant vídeo format 9:16 amb vora blanca...")
        out_path = os.path.join(TMP_DIR, "output_standard.mp4")
        cmd = [
            "ffmpeg", "-y", "-i", raw_path,
            "-filter_complex",
            "[0:v]scale=960:1700:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=white[v]",
            "-map", "[v]", "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
            out_path
        ]
        subprocess.run(cmd, check=True)
        output_files.append((out_path, ""))

    return output_files

# ==========================================
# HOSTING ORPHAN BRANCH
# ==========================================
def upload_to_media_branch(local_file, remote_filename):
    print(f"📦 Pujant {remote_filename} a la branca orphan 'media'...")
    subprocess.run(["git", "config", "user.name", "CarpetBot"], check=True)
    subprocess.run(["git", "config", "user.email", "bot@github.com"], check=True)
    subprocess.run(["git", "checkout", "--orphan", "media"], check=True)
    subprocess.run(["git", "rm", "-rf", "."], check=False)

    dest = os.path.join(".", remote_filename)
    subprocess.run(["cp", local_file, dest], check=True)

    subprocess.run(["git", "add", remote_filename], check=True)
    subprocess.run(["git", "commit", "-m", f"Host video {remote_filename}"], check=True)
    subprocess.run(["git", "push", "-f", "origin", "media"], check=True)
    subprocess.run(["git", "checkout", "main"], check=True)

    return f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/media/{remote_filename}"

# ==========================================
# PUBLICACIÓ VIA ZERNIO
# ==========================================
def publish_to_zernio(video_url, title):
    if not ZERNIO_API_KEY:
        print("❌ ZERNIO_API_KEY no definida.")
        return False

    url = "https://api.zernio.com/v1/posts"
    headers = {
        "Authorization": f"Bearer {ZERNIO_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "body": {
            "content": f"{title} #carpetcleaning #satisfying #asmr #restoration",
            "mediaItems": [{"type": "video", "url": video_url}],
            "platforms": [{"platform": "instagram"}, {"platform": "facebook"}],
            "publishNow": True
        }
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        return r.status_code in [200, 201]
    except Exception as e:
        print(f"Avís Zernio: {e}")
        return False

# ==========================================
# FLUX PRINCIPAL
# ==========================================
def main():
    target = get_best_video()
    if not target:
        return

    print(f"🎯 Vídeo triat: ID {target['id']} de {target['account']} ({target['views']} views)")

    raw_path = os.path.join(TMP_DIR, "raw_video.mp4")

    # Descàrrega segons el motor que l'ha trobat
    if target["engine"] == "tikwm":
        print(f"⬇️ Descarregant via TikWM...")
        r = requests.get(target["download_url"], headers=HEADERS, stream=True, timeout=60)
        with open(raw_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
    else:
        print(f"⬇️ Descarregant via yt-dlp...")
        ydl_opts = {'outtmpl': raw_path, 'quiet': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([target["download_url"]])

    # Processar vídeo amb FFmpeg
    processed_list = process_video(raw_path, target["duration"])

    # Enviar a Telegram o publicar
    for file_path, label in processed_list:
        post_title = f"{target['title']} {label}".strip()

        if MODE_PROVA:
            print(f"🧪 [MODE PROVA] Enviant {file_path} a Telegram...")
            send_telegram_video(file_path, caption=f"🧪 Carpet Cleaning Test\n\n📌 {post_title}\n👀 {target['views']:,} visualitzacions")
        else:
            filename = os.path.basename(file_path)
            public_url = upload_to_media_branch(file_path, filename)
            ok = publish_to_zernio(public_url, post_title)
            if ok:
                send_telegram_msg(f"✅ <b>Publicat a Xarxes!</b>\n\n📌 {post_title}\n👀 {target['views']:,} visualitzacions")
            else:
                send_telegram_msg(f"❌ Error publicant a Zernio: {post_title}")

    # Guardar ID a l'historial
    history = load_history()
    history.append(target["id"])
    save_history(history)
    print("✨ Procés finalitzat amb èxit!")

if __name__ == "__main__":
    main()