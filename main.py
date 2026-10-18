import os
import sys
import json
import subprocess
import requests
import pandas as pd

# ==========================================
# CONFIGURACIÓ PRINCIPAL
# ==========================================
MODE_PROVA = True  # True = envia MP4 a Telegram. False = puja a Zernio i avisa per Telegram.

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ZERNIO_API_KEY = os.getenv("ZERNIO_API_KEY")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "usuari/carpetcleaning")

HISTORY_FILE = "history.json"
ACCOUNTS_FILE = "accounts.csv"
TMP_DIR = "temp_media"

os.makedirs(TMP_DIR, exist_ok=True)

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
        print(f"Error enviant text a Telegram: {e}")

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
            res = requests.post(url, data=data, files=files, timeout=120)
            if res.status_code == 200:
                print("✅ Vídeo enviat a Telegram correctament!")
            else:
                print(f"❌ Error Telegram ({res.status_code}): {res.text}")
    except Exception as e:
        print(f"Error enviant fitxer de vídeo a Telegram: {e}")

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
# TIKTOK SCRAPER VIA TIKWM
# ==========================================
def get_best_video():
    if not os.path.exists(ACCOUNTS_FILE):
        print(f"❌ No s'ha trobat {ACCOUNTS_FILE}")
        return None

    df = pd.read_csv(ACCOUNTS_FILE)
    accounts = df["username"].dropna().str.replace("@", "").tolist()
    history = load_history()

    candidates = []

    for acc in accounts:
        print(f"🔍 Comprovant TikTok: @{acc}...")
        url = f"https://tikwm.com/api/user/posts?unique_id={acc}&count=15"
        try:
            res = requests.get(url, timeout=15).json()
            if res.get("code") != 0 or "data" not in res or "videos" not in res["data"]:
                continue

            for v in res["data"]["videos"]:
                vid_id = str(v.get("id"))
                duration = v.get("duration", 0)

                # Regla: vídeos de més de 10 minuts es descarten
                if vid_id in history or duration > 600 or duration == 0:
                    continue

                candidates.append({
                    "id": vid_id,
                    "account": acc,
                    "title": v.get("title", "Satisfying Carpet Cleaning"),
                    "views": v.get("play_count", 0),
                    "duration": duration,
                    "download_url": v.get("play")  # Sense marca d'aigua
                })
        except Exception as e:
            print(f"Error consultant @{acc}: {e}")

    if not candidates:
        print("ℹ️ No hi ha vídeos nous o aptes per descarregar.")
        return None

    # Ordenar pel més viral (més views)
    candidates.sort(key=lambda x: x["views"], reverse=True)
    return candidates[0]

# ==========================================
# PROCESSAMENT FFMPEG
# ==========================================
def process_video(raw_path, duration):
    """
    Retorna una llista de tuples: [(path_video_processat, titol_part)]
    """
    output_files = []
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    # CAS A: Vídeo entre 1m 29s (89s) i 2 minuts (120s) -> accelerar per encabir-lo en 88s
    if 89 < duration <= 120:
        print(f"⚡ Accelerant vídeo de {duration}s a 88s...")
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

    # CAS B: Vídeo de més de 2 minuts (i <= 10 minuts) -> tallar en parts d'1m 20s (80s)
    elif duration > 120:
        chunk_len = 80
        total_parts = int((duration // chunk_len) + (1 if duration % chunk_len > 0 else 0))
        print(f"✂️ Vídeo llarg ({duration}s). Es dividirà en {total_parts} parts d'1m 20s...")

        for idx in range(total_parts):
            start = idx * chunk_len
            part_num = idx + 1
            part_text = f"Part {part_num}"
            out_path = os.path.join(TMP_DIR, f"output_part_{part_num}.mp4")

            # Filtre: Retalla 80s + escala a 960x1700 + fons 1080x1920 blanc + text superior
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

    # CAS C: Vídeo normal (< 89s)
    else:
        print("📐 Processant vídeo estàndard 9:16 amb vora blanca...")
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
# HOSTING TEMPORAL EN ORPHAN BRANCH (MEDIA)
# ==========================================
def upload_to_media_branch(local_file, remote_filename):
    """
    Fa un push forçat d'un sol fitxer a la branca 'media' per no inflar l'historial.
    """
    print(f"📦 Pujant {remote_filename} a la branca orphan 'media'...")
    subprocess.run(["git", "config", "user.name", "CarpetBot"], check=True)
    subprocess.run(["git", "config", "user.email", "bot@github.com"], check=True)
    subprocess.run(["git", "checkout", "--orphan", "media"], check=True)
    subprocess.run(["git", "rm", "-rf", "."], check=False)
    
    # Copiar fitxer
    dest = os.path.join(".", remote_filename)
    subprocess.run(["cp", local_file, dest], check=True)
    
    subprocess.run(["git", "add", remote_filename], check=True)
    subprocess.run(["git", "commit", "-m", f"Host video {remote_filename}"], check=True)
    subprocess.run(["git", "push", "-f", "origin", "media"], check=True)
    
    # Tornar a main
    subprocess.run(["git", "checkout", "main"], check=True)

    public_url = f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/media/{remote_filename}"
    return public_url

# ==========================================
# PUBLICACIÓ VIA ZERNIO
# ==========================================
def publish_to_zernio(video_url, title):
    if not ZERNIO_API_KEY:
        print("❌ ZERNIO_API_KEY no està definida.")
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
            "platforms": [
                {"platform": "instagram"},
                {"platform": "facebook"}
            ],
            "publishNow": True
        }
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        return r.status_code in [200, 201]
    except Exception as e:
        print(f"Error cridant Zernio: {e}")
        return False

# ==========================================
# FLUX PRINCIPAL
# ==========================================
def main():
    target = get_best_video()
    if not target:
        return

    print(f"🎯 Seleccionat: ID {target['id']} de @{target['account']} ({target['views']} views, {target['duration']}s)")

    # 1. Descarregar vídeo original
    raw_path = os.path.join(TMP_DIR, "raw_video.mp4")
    print(f"⬇️ Descarregant de TikWM...")
    r = requests.get(target["download_url"], stream=True, timeout=60)
    with open(raw_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)

    # 2. Processar amb FFmpeg segons les regles de durada
    processed_list = process_video(raw_path, target["duration"])

    # 3. Publicar o Mode Prova
    for file_path, label in processed_list:
        post_title = f"{target['title']} {label}".strip()

        if MODE_PROVA:
            print(f"🧪 [MODE PROVA] Enviant {file_path} a Telegram...")
            send_telegram_video(file_path, caption=f"🧪 Prova CarpetCleaning\n\n📌 {post_title}\n👀 {target['views']:,} views")
        else:
            filename = os.path.basename(file_path)
            public_url = upload_to_media_branch(file_path, filename)
            ok = publish_to_zernio(public_url, post_title)
            if ok:
                send_telegram_msg(f"✅ <b>Publicat a Xarxes!</b>\n\n📌 {post_title}\n👀 Views originals: {target['views']:,}")
            else:
                send_telegram_msg(f"❌ Error en publicar a Zernio: {post_title}")

    # 4. Afegir a l'historial per no repetir mai
    history = load_history()
    history.append(target["id"])
    save_history(history)
    print("✨ Procés completat correctament!")

if __name__ == "__main__":
    main()