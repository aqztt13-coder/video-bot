
"""
Telegram Media Downloader Bot

Supported platforms:
YouTube, TikTok, Instagram, Pinterest, Facebook, X and others.

Features:
- YouTube links are downloaded automatically as MP3.
- Other platforms show Video / MP3 buttons.
- Uses yt-dlp, FFmpeg and python-telegram-bot.
"""

import asyncio
import logging
import os
import re
import shutil
import tempfile

from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from dotenv import load_dotenv

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)

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


# =========================================================
# Configuration
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

COOKIES_FILE = os.getenv("COOKIES_FILE")

MAX_SIZE_MB = int(os.getenv("MAX_SIZE_MB", "50"))

MAX_PARALLEL = int(os.getenv("MAX_PARALLEL", "3"))

MAX_SIZE = MAX_SIZE_MB * 1024 * 1024


# =========================================================
# Logging
# =========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

log = logging.getLogger("video-bot")


# =========================================================
# FFmpeg detection
# =========================================================

HAS_FFMPEG = shutil.which("ffmpeg") is not None

if not HAS_FFMPEG:
    log.warning(
        "FFmpeg is not installed. MP3 conversion is unavailable."
    )


# =========================================================
# Downloader settings
# =========================================================

SEM = asyncio.Semaphore(MAX_PARALLEL)

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

SUPPORTED = (
    "YouTube • TikTok • Instagram • Pinterest • "
    "Facebook • X (Twitter)"
)


# =========================================================
# URL detection
# =========================================================

def is_youtube_url(url: str) -> bool:
    """
    Check whether the URL belongs to YouTube.
    """

    try:
        hostname = (urlparse(url).hostname or "").lower()

        return (
            hostname == "youtube.com"
            or hostname.endswith(".youtube.com")
            or hostname == "youtu.be"
            or hostname == "youtube-nocookie.com"
            or hostname.endswith(".youtube-nocookie.com")
        )

    except Exception:
        return False


# =========================================================
# Download function
# =========================================================

def download_media(url: str, mode: str, outdir: str) -> Path:
    """
    Download media using yt-dlp.

    mode:
        audio = MP3
        video = MP4

    This function runs inside a separate thread.
    """

    if mode == "audio" and not HAS_FFMPEG:
        raise RuntimeError(
            "FFmpeg is required to convert audio to MP3. "
            "Please install FFmpeg first."
        )

    output_template = str(
        Path(outdir) / "%(id)s.%(ext)s"
    )

    opts = {
        "outtmpl": output_template,

        "noplaylist": True,

        "quiet": True,

        "no_warnings": True,

        "max_filesize": MAX_SIZE,

        "socket_timeout": 30,

        "retries": 3,

        "fragment_retries": 3,

        "restrictfilenames": True,

        "ignoreerrors": False,
    }

    # Optional cookies support
    if COOKIES_FILE and Path(COOKIES_FILE).exists():
        opts["cookiefile"] = COOKIES_FILE

    # -----------------------------------------------------
    # Audio / MP3
    # -----------------------------------------------------

    if mode == "audio":

        opts.update({

            "format": "bestaudio/best",

            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",

                    "preferredcodec": "mp3",

                    "preferredquality": "192",
                }
            ],

        })

    # -----------------------------------------------------
    # Video
    # -----------------------------------------------------

    else:

        if HAS_FFMPEG:

            opts.update({

                "format": (
                    "bv*[height<=720][ext=mp4]+ba[ext=m4a]/"
                    "b[height<=720][ext=mp4]/"
                    "b[height<=720]/b"
                ),

                "merge_output_format": "mp4",

            })

        else:

            # Without FFmpeg, download a single combined file.

            opts.update({

                "format": (
                    "b[height<=720][ext=mp4]/"
                    "b[ext=mp4]/b"
                ),

            })

    # -----------------------------------------------------
    # Execute download
    # -----------------------------------------------------

    with yt_dlp.YoutubeDL(opts) as ydl:

        ydl.extract_info(
            url,
            download=True
        )

    # -----------------------------------------------------
    # Find downloaded file
    # -----------------------------------------------------

    if mode == "audio":

        allowed_extensions = {".mp3"}

    else:

        allowed_extensions = {
            ".mp4",
            ".mkv",
            ".webm",
            ".mov",
        }

    files = [
        p
        for p in Path(outdir).iterdir()

        if (
            p.is_file()
            and p.suffix.lower() in allowed_extensions
        )
    ]

    if not files:

        raise FileNotFoundError(
            "No output file was generated. "
            "The media may be unavailable or unsupported."
        )

    # Get the largest valid output file

    output_file = max(
        files,
        key=lambda p: p.stat().st_size
    )

    if output_file.stat().st_size > MAX_SIZE:

        raise ValueError(
            f"File exceeds the maximum size of {MAX_SIZE_MB} MB."
        )

    return output_file


# =========================================================
# Start command
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "👋 أهلاً بك في بوت تنزيل الوسائط!\n\n"

        "أرسل رابط الفيديو وسأقوم بتنزيله لك.\n\n"

        "🎵 روابط YouTube يتم تنزيلها تلقائياً بصيغة MP3.\n\n"

        "🎬 بقية المنصات تتيح لك الاختيار بين الفيديو والصوت.\n\n"

        f"المنصات المدعومة:\n{SUPPORTED}\n\n"

        f"⚠️ الحد الأقصى لحجم الملف: {MAX_SIZE_MB} MB"

    )


# =========================================================
# Help command
# =========================================================

async def help_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "📖 طريقة الاستخدام:\n\n"

        "1️⃣ أرسل رابط YouTube للحصول على MP3 مباشرة.\n\n"

        "2️⃣ أرسل رابط منصة أخرى واختر الفيديو أو MP3.\n\n"

        "3️⃣ انتظر حتى يكتمل التنزيل.\n\n"

        "4️⃣ سيصلك الملف في المحادثة.\n\n"

        f"الحد الأقصى: {MAX_SIZE_MB} MB"

    )


# =========================================================
# Main download processing
# =========================================================

async def process_download(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    mode: str,
):

    chat_id = update.effective_chat.id

    # Create status message

    if mode == "audio":

        status_text = "⏳ جارٍ تنزيل الصوت وتحويله إلى MP3..."

    else:

        status_text = "⏳ جارٍ تنزيل الفيديو..."

    status = await context.bot.send_message(

        chat_id=chat_id,

        text=status_text

    )

    tmpdir = tempfile.mkdtemp(
        prefix="vbot_"
    )

    try:

        # Check FFmpeg before starting MP3 download

        if mode == "audio" and not HAS_FFMPEG:

            await status.edit_text(

                "❌ لا يمكن تحويل الصوت إلى MP3.\n\n"

                "يرجى تثبيت FFmpeg على الخادم أو الكمبيوتر."

            )

            return

        # Limit simultaneous downloads

        async with SEM:

            # Download media

            path = await asyncio.to_thread(

                download_media,

                url,

                mode,

                tmpdir

            )

            size = path.stat().st_size

            # Check file size

            if size > MAX_SIZE:

                await status.edit_text(

                    f"❌ حجم الملف أكبر من الحد المسموح.\n\n"

                    f"حجم الملف: {size / (1024 * 1024):.2f} MB\n"

                    f"الحد الأقصى: {MAX_SIZE_MB} MB"

                )

                return

            # Update status

            await status.edit_text(
                "📤 جارٍ إرسال الملف..."
            )

            # Send file

            with open(path, "rb") as file:

                if mode == "audio":

                    await context.bot.send_audio(

                        chat_id=chat_id,

                        audio=file,

                        filename=path.name,

                        title=path.stem,

                        read_timeout=300,

                        write_timeout=300,

                    )

                else:

                    await context.bot.send_video(

                        chat_id=chat_id,

                        video=file,

                        supports_streaming=True,

                        filename=path.name,

                        read_timeout=300,

                        write_timeout=300,

                    )

        # Delete status message

        await status.delete()

        log.info(
            "Successfully sent %s to chat %s",
            path.name,
            chat_id
        )

    except yt_dlp.utils.DownloadError as e:

        log.error(
            "DownloadError for %s: %s",
            url,
            e
        )

        reason = re.sub(
            r"\x1b\[[0-9;]*m",
            "",
            str(e)
        )[-400:]

        await status.edit_text(

            "❌ تعذّر تنزيل هذا الرابط.\n\n"

            f"السبب:\n{reason}"

        )

    except FileNotFoundError as e:

        log.error(
            "File not found: %s",
            e
        )

        await status.edit_text(
            f"❌ {e}"
        )

    except ValueError as e:

        log.error(
            "File validation error: %s",
            e
        )

        await status.edit_text(
            f"❌ {e}"
        )

    except Exception as e:

        log.exception(
            "Unexpected error during download"
        )

        await status.edit_text(

            "❌ حدث خطأ غير متوقع.\n\n"

            f"النوع: {type(e).__name__}\n"

            f"التفاصيل: {str(e)[:300]}"

        )

    finally:

        # Remove temporary files

        shutil.rmtree(
            tmpdir,
            ignore_errors=True
        )


# =========================================================
# Handle incoming URLs
# =========================================================

async def handle_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.message

    if not message or not message.text:
        return

    match = URL_RE.search(message.text)

    if not match:

        await message.reply_text(
            "❌ أرسل رابطاً صالحاً يبدأ بـ http أو https."
        )

        return

    # Remove common trailing punctuation

    url = match.group(0).rstrip(
        ".,!?،؛:)]}»"
    )

    # -----------------------------------------------------
    # YouTube: Automatically download MP3
    # -----------------------------------------------------

    if is_youtube_url(url):

        await process_download(

            update,

            context,

            url,

            "audio"

        )

        return

    # -----------------------------------------------------
    # Other platforms: Show download options
    # -----------------------------------------------------

    message_id = message.message_id

    context.user_data.setdefault(
        "urls",
        {}
    )[message_id] = url

    keyboard = InlineKeyboardMarkup([

        [

            InlineKeyboardButton(

                "🎬 فيديو",

                callback_data=f"video|{message_id}"

            ),

            InlineKeyboardButton(

                "🎵 صوت MP3",

                callback_data=f"audio|{message_id}"

            ),

        ]

    ])

    await message.reply_text(

        "ماذا تريد أن أنزّل؟",

        reply_markup=keyboard,

        reply_to_message_id=message_id

    )


# =========================================================
# Handle button selection
# =========================================================

async def on_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    try:

        mode, msg_id = query.data.split("|")

        msg_id = int(msg_id)

    except (ValueError, AttributeError):

        await query.edit_message_text(
            "❌ طلب غير صالح."
        )

        return

    url = context.user_data.get(
        "urls",
        {}
    ).get(msg_id)

    if not url:

        await query.edit_message_text(

            "⚠️ انتهت صلاحية الطلب.\n"

            "يرجى إرسال الرابط مرة أخرى."

        )

        return

    # Remove buttons

    await query.delete_message()

    try:

        await process_download(

            update,

            context,

            url,

            mode

        )

    finally:

        # Remove saved URL

        context.user_data.get(
            "urls",
            {}
        ).pop(
            msg_id,
            None
        )


# =========================================================
# Main application
# =========================================================

def main():

    if not BOT_TOKEN:

        raise SystemExit(
            "❌ Please set BOT_TOKEN in your .env file."
        )

    # HTTPX request configuration

    trequest = HTTPXRequest(

        connection_pool_size=8,

        connect_timeout=60.0,

        read_timeout=300.0,

        write_timeout=300.0,

        media_write_timeout=300.0,

    )

    # Create Telegram application

    app = (

        Application.builder()

        .token(BOT_TOKEN)

        .request(trequest)

        .concurrent_updates(True)

        .build()

    )

    # Register commands

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("help", help_cmd)
    )

    # Register URL handler

    app.add_handler(

        MessageHandler(

            filters.TEXT & ~filters.COMMAND,

            handle_link

        )

    )

    # Register download buttons

    app.add_handler(

        CallbackQueryHandler(

            on_choice,

            pattern=r"^(video|audio)\|"

        )

    )

    # Start bot

    log.info(
        "Telegram downloader bot is running..."
    )

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# =========================================================
# Entry point
# =========================================================

if __name__ == "__main__":

    main()
