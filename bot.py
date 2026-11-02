"""
بوت تيليجرام لتنزيل الفيديوهات من يوتيوب، تيك توك، إنستغرام، بينترست، فيسبوك، إكس وغيرها.
يعتمد على: python-telegram-bot + yt-dlp + ffmpeg
"""

import asyncio
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

import yt_dlp
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
COOKIES_FILE = os.getenv("COOKIES_FILE")  # اختياري (إنستغرام/فيسبوك/إكس أحياناً تحتاجه)
MAX_SIZE_MB = int(os.getenv("MAX_SIZE_MB", "50"))  # حد تيليجرام للبوتات = 50MB
MAX_PARALLEL = int(os.getenv("MAX_PARALLEL", "3"))  # عدد التنزيلات المتزامنة
MAX_SIZE = MAX_SIZE_MB * 1024 * 1024

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO
)
log = logging.getLogger("video-bot")

HAS_FFMPEG = shutil.which("ffmpeg") is not None
if not HAS_FFMPEG:
    log.warning("ffmpeg غير مثبّت! سيعمل البوت بجودة محدودة وبدون تحويل MP3.")

URL_RE = re.compile(r"https?://[^\s]+")
SEM = asyncio.Semaphore(MAX_PARALLEL)

SUPPORTED = "يوتيوب • تيك توك • إنستغرام • بينترست • فيسبوك • إكس (تويتر)"


# ------------------------------------------------------------------ التنزيل
def download_media(url: str, mode: str, outdir: str) -> Path:
    """ينزّل الملف داخل outdir ويرجع مساره (يعمل في thread منفصل)."""
    opts = {
        "outtmpl": str(Path(outdir) / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": MAX_SIZE,
        "socket_timeout": 30,
        "retries": 3,
        "restrictfilenames": True,
        # خداع يوتيوب بطلب الفيديو كأنه هاتف أندرويد لتجاوز حظر الخوادم السحابية
        "extractor_args": {"youtube": ["player_client=android"]},
    }
    if COOKIES_FILE and Path(COOKIES_FILE).exists():
        opts["cookiefile"] = COOKIES_FILE

    if mode == "audio":
        if HAS_FFMPEG:
            opts.update(
                format="bestaudio/best",
                postprocessors=[
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            )
        else:
            opts.update(format="bestaudio[ext=m4a]/bestaudio")
    else:
        if HAS_FFMPEG:
            opts.update(
                format=(
                    "bv*[height<=720][ext=mp4]+ba[ext=m4a]/"
                    "b[height<=720][ext=mp4]/b[height<=720]/b"
                ),
                merge_output_format="mp4",
            )
        else:
            # بدون ffmpeg: نأخذ ملفاً واحداً جاهزاً (صوت + فيديو معاً)
            opts.update(format="b[height<=720][ext=mp4]/b[ext=mp4]/b")

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)

    files = [
        p for p in Path(outdir).iterdir()
        if p.is_file() and p.suffix not in {".part", ".ytdl", ".json"}
    ]
    if not files:
        raise FileNotFoundError("لم يتم إنتاج أي ملف (قد يكون أكبر من الحد المسموح).")
    return max(files, key=lambda p: p.stat().st_size)


# ------------------------------------------------------------------ الأوامر
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 أهلاً بك!\n\n"
        "أرسل لي رابط أي فيديو وسأحمّله لك.\n\n"
        f"المنصات المدعومة:\n{SUPPORTED}\n\n"
        f"⚠️ الحد الأقصى لحجم الملف: {MAX_SIZE_MB}MB"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "1️⃣ أرسل رابط الفيديو\n"
        "2️⃣ اختر (فيديو) أو (صوت MP3)\n"
        "3️⃣ انتظر ثواني وسيصلك الملف"
    )


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    match = URL_RE.search(update.message.text or "")
    if not match:
        await update.message.reply_text("❌ أرسل رابطاً صالحاً يبدأ بـ http أو https.")
        return

    url = match.group(0)
    # نخزّن الرابط حسب رقم الرسالة ليرتبط بالأزرار
    context.user_data.setdefault("urls", {})[update.message.message_id] = url

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎬 فيديو", callback_data=f"video|{update.message.message_id}"
                ),
                InlineKeyboardButton(
                    "🎵 صوت MP3", callback_data=f"audio|{update.message.message_id}"
                ),
            ]
        ]
    )
    await update.message.reply_text(
        "ماذا تريد أن أنزّل؟", reply_markup=keyboard, reply_to_message_id=update.message.message_id
    )


async def on_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    mode, msg_id = query.data.split("|")
    url = context.user_data.get("urls", {}).get(int(msg_id))
    if not url:
        await query.edit_message_text("⚠️ انتهت صلاحية الطلب، أعد إرسال الرابط.")
        return

    await query.edit_message_text("⏳ جارِ التنزيل... قد يستغرق بعض الوقت.")
    chat_id = query.message.chat_id
    tmpdir = tempfile.mkdtemp(prefix="vbot_")

    try:
        async with SEM:
            await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
            path = await asyncio.to_thread(download_media, url, mode, tmpdir)

            size = path.stat().st_size
            if size > MAX_SIZE:
                await query.edit_message_text(
                    f"❌ حجم الملف ({size // (1024 * 1024)}MB) أكبر من الحد المسموح ({MAX_SIZE_MB}MB)."
                )
                return

            await query.edit_message_text("📤 جارِ الرفع...")
            with open(path, "rb") as f:
                if mode == "audio":
                    await context.bot.send_audio(chat_id, f, filename=path.name, read_timeout=300, write_timeout=300)
                else:
                    await context.bot.send_video(
                        chat_id, f, supports_streaming=True, filename=path.name, read_timeout=300, write_timeout=300
                    )
        await query.delete_message()

    except yt_dlp.utils.DownloadError as e:
        log.error("DownloadError for %s: %s", url, e)
        reason = re.sub(r"\x1b\[[0-9;]*m", "", str(e))[-400:]
        await query.edit_message_text(f"❌ تعذّر تنزيل هذا الرابط.\n\nالسبب:\n{reason}")
    except FileNotFoundError as e:
        log.error("No output file for %s: %s", url, e)
        await query.edit_message_text(f"❌ {e}")
    except Exception as e:  # noqa: BLE001
        log.exception("Unexpected error")
        await query.edit_message_text(f"❌ خطأ غير متوقع:\n{type(e).__name__}: {str(e)[:300]}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        context.user_data.get("urls", {}).pop(int(msg_id), None)


# ------------------------------------------------------------------ التشغيل
def main():
    if not BOT_TOKEN:
        raise SystemExit("ضع BOT_TOKEN في ملف .env أو في Environment Variables")

    # إعداد HTTPXRequest لزيادة مهلة الاتصال والرفع (تجنب أخطاء TimedOut و ReadError)
    trequest = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=60.0,
        read_timeout=300.0,
        write_timeout=300.0
    )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(trequest)
        .concurrent_updates(True)
        .build()
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(on_choice, pattern=r"^(video|audio)\|"))

    log.info("Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
