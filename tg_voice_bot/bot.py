import os, asyncio, tempfile, subprocess, shutil, html, logging, socket, threading
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
)
import speech_recognition as sr
import edge_tts
import gtts
from voice_clone import clone_voice, warmup, shutdown, has_ref, save_ref, status as xtts_status

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
GPT_KEY = os.getenv("OPENAI_API_KEY") or ""


def find_ffmpeg() -> str | None:
    p = shutil.which("ffmpeg")
    if p:
        return p
    links = Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"
    return str(links) if links.exists() else None


FFMPEG = find_ffmpeg()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
log = logging.getLogger("bot")

VOICES = {
    "clone": ("ru-RU-SvetlanaNeural", "-10%", "+0Hz"),
    "soft": ("ru-RU-SvetlanaNeural", "-22%", "+4Hz"),
    "calm": ("ru-RU-SvetlanaNeural", "-12%", "+6Hz"),
    "warm": ("ru-RU-DmitryNeural", "-18%", "-8Hz"),
}

SYS_PROMPT = (
    "Ты голосовой ассистент в Telegram, и звучишь как близкий человек, "
    "который заботливо и с теплотой говорит с дорогим человеком. "
    "Ты мягкая, спокойная, с материнской лаской в голосе, но без сюсюканья. "
    "Как будто рядом наконец выдохнул и можно расслабиться. "
    "Правила ответа: одно-два коротких предложения, простые слова, "
    "без markdown, списков, эмодзи и сухих канцеляризмов. "
    "Можно обращаться тепло: «знаешь», «представь», «давай просто», "
    "«тебе понравится». Не задавай вопросов ради вопросов и не советуй, "
    "если человек этого не просил."
)


async def synth(text: str, voice: str) -> str:
    # 1. Клон голоса (XTTS) — если есть образец
    if voice == "clone":
        p = await asyncio.to_thread(clone_voice, text)
        if p:
            return p
        log.warning("клон недоступен, переключаюсь на edge-tts")

    # 2. edge-tts — натуральные нейросетевые голоса (может быть заблокирован регионально)
    out = Path(tempfile.gettempdir()) / f"tgv_{abs(hash(text))}.mp3"
    name, rate, pitch = VOICES.get(voice, VOICES.get("soft", VOICES["calm"]))
    try:
        await edge_tts.Communicate(text, name, rate=rate, pitch=pitch).save(str(out))
        return str(out)
    except Exception as e:
        log.warning("edge-tts недоступен (%s), переключаюсь на gTTS", type(e).__name__)

    # 3. gTTS — работает всегда, но звучит роботично
    out.unlink(missing_ok=True)
    await asyncio.to_thread(gtts.gTTS(text, lang="ru").save, str(out))
    return str(out)


def ogg_to_wav(ogg: str) -> str:
    wav = ogg.rsplit(".", 1)[0] + ".wav"
    subprocess.run(
        [FFMPEG, "-y", "-i", ogg, "-ar", "16000", "-ac", "1", wav],
        capture_output=True, check=True,
    )
    return wav


def transcribe(wav: str) -> str:
    r = sr.Recognizer()
    with sr.AudioFile(wav) as src:
        return r.recognize_google(r.record(src), language="ru-RU")


async def ask_llm(uid: int, text: str, ctx) -> str:
    if not GPT_KEY:
        return f"Услышал: {text}. Ключ OpenAI не задан, поэтому отвечаю эхом."
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=GPT_KEY)

    hist = ctx.user_data.setdefault("history", [])
    hist.append({"role": "user", "content": text})

    r = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": SYS_PROMPT}, *hist[-10:]],
    )
    reply = r.choices[0].message.content
    hist.append({"role": "assistant", "content": reply})
    return html.unescape(reply)


async def cmd_start(u: Update, _):
    await u.message.reply_text(
        "Привет. Я здесь, если что.\n\n"
        "Как пользоваться:\n"
        "- запиши голосовое, отвечу голосом\n"
        "- или просто напиши текстом\n\n"
        "Команды:\n"
        "/voice clone|soft|calm|warm - сменить тембр\n"
        "/ref - прислать голос, который нужно клонировать\n"
        "/status - что внутри\n"
        "/reset - забыть контекст"
    )


async def cmd_voice(u: Update, ctx):
    v = (u.message.text or "").split()[-1].lower()
    if v not in VOICES:
        await u.message.reply_text(f"Доступно: {', '.join(VOICES)}")
        return
    if v == "clone" and not has_ref():
        return await u.message.reply_text(
            "Клон ещё не настроен. Пришли /ref и голосовое с тем голосом — запомню."
        )
    ctx.user_data["voice"] = v
    await u.message.reply_text(f"Хорошо, буду звучать иначе: {v}")


async def cmd_ref(u: Update, ctx):
    if u.message.text and u.message.text.strip().split()[-1].lower() in ("off", "clear", "нет"):
        try:
            from voice_clone import REF
            REF.unlink(missing_ok=True)
        except OSError:
            pass
        ctx.user_data.pop("expect_ref", None)
        return await u.message.reply_text("Образец удалён, вернусь к обычным голосам.")
    ctx.user_data["expect_ref"] = True
    await u.message.reply_text(
        "Пришли голосовое сообщение голосом, который нужно клонировать — "
        "до 45 секунд спокойной речи, без музыки и шума. Запомню и буду им звучать."
    )


async def cmd_status(u: Update, _):
    st = xtts_status()
    ref = st["ref"] or "не задан (пришли /ref)"
    mem = f"свободно {st['mem_mb']} МБ" if st["mem_mb"] else "свободно н/д"
    rss = f", процесс {st['rss_mb']} МБ" if st.get("rss_mb") else ""
    await u.message.reply_text(
        f"Синтез: {st['backend']}, режим {st['mode']}, {st['threads']} потока(ов)\n"
        f"Память: {mem}{rss}\n"
        f"Образец: {ref}\n"
        f"Модель: {st['model']}"
        + (f"\nОшибка: {st['fail']}" if st["fail"] else "")
    )


async def cmd_reset(u: Update, ctx):
    ctx.user_data.pop("history", None)
    await u.message.reply_text("Всё, забыла. Начинаем с чистого листа.")


async def handle_voice(u: Update, ctx):
    if not FFMPEG:
        return await u.message.reply_text(
            "Не найден ffmpeg. Поставь его и добавь в PATH - без него я не слышу голосовые."
        )
    m = u.message
    ogg = Path(tempfile.gettempdir()) / f"in_{m.message_id}.ogg"
    await (await m.get_file()).download_to_drive(str(ogg))

    if ctx.user_data.pop("expect_ref", None):
        try:
            ref = save_ref(ogg)
        except Exception as e:
            log.warning("ref save fail: %s", e)
            return await m.reply_text(
                "Не получилось разобрать запись. Нужно голосовое до 45 секунд, "
                "где слышно только голос."
            )
        finally:
            if ogg.exists():
                os.remove(ogg)
        ctx.user_data["voice"] = "clone"
        return await m.reply_text(
            f"Запомнила этот голос ({ref.stat().st_size // 1024} КБ). "
            "Первый ответ будет долгим — модель грузится. Дальше быстрее."
        )

    try:
        text = await asyncio.to_thread(transcribe, ogg_to_wav(str(ogg)))
    except sr.UnknownValueError:
        return await m.reply_text("Я тебя не расслышала. Скажи ещё раз, поближе к микрофону.")
    except Exception as e:
        log.warning("stt fail: %s", e)
        return await m.reply_text("Запись получилась битая. Попробуем ещё разок?")

    await m.reply_text(f"Ты: {text}")

    reply = await ask_llm(m.from_user.id, text, ctx)
    mp3 = await synth(reply, ctx.user_data.get("voice", "soft"))
    await m.reply_voice_note(mp3)

    for p in (ogg, Path(str(ogg).replace(".ogg", ".wav"))):
        if p.exists():
            os.remove(p)


async def handle_text(u: Update, ctx):
    reply = await ask_llm(u.from_user.id, u.message.text, ctx)
    mp3 = await synth(reply, ctx.user_data.get("voice", "soft"))
    await u.message.reply_voice_note(mp3)


async def error_handler(_, ctx):
    log.error("crash: %s", ctx.error)


def check_connection(timeout: int = 12) -> str | None:
    """Проверяет доступ к Telegram до старта. Возвращает текст проблемы или None."""
    host = "api.telegram.org"
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return f"DNS не отвечает: не удалось разрешить {host}. Проверь интернет или VPN."
    try:
        s = socket.create_connection((host, 443), timeout=timeout)
        s.close()
    except TimeoutError:
        return f"{host}:443 не отвечает. Нужен VPN с зарубежным сервером."
    except OSError as e:
        return f"Не удалось подключиться к {host}: {e}"
    finally:
        socket.setdefaulttimeout(None)
    return None


async def on_startup(app):
    """Команды в меню + снос webhook: polling и webhook несовместимы.

    Если на бота раньше ставили webhook (другой сервис, BotFather, старый деплой),
    getUpdates отвечает Conflict, и бот молча не видит сообщения.
    """
    await app.bot.set_my_commands([
        BotCommand("start", "старт"),
        BotCommand("voice", "сменить тембр"),
        BotCommand("ref", "прислать голос для клона"),
        BotCommand("status", "что внутри"),
        BotCommand("reset", "очистить контекст"),
    ])
    try:
        info = await app.bot.get_webhook_info()
        if info.url:
            await app.bot.delete_webhook(drop_pending_updates=True)
            log.warning("webhook %s был активен — снесён, иначе был бы Conflict", info.url)
        else:
            log.info("webhook не установлен, polling")
    except Exception as e:
        log.warning("не смог проверить webhook: %s", e)


def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN не найден. Открой файл .env и вставь токен от BotFather.")
    if not FFMPEG:
        log.warning("ffmpeg не найден - голосовые работать не будут. Текст будет работать.")

    # Проверяем сеть ДО старта, иначе python-telegram-bot сыпет трейсбеком
    problem = check_connection()
    if problem:
        print("\n" + "=" * 60)
        print("  Бот не может подключиться к Telegram")
        print("=" * 60)
        print(f"  {problem}\n")
        print("  Что делать:")
        print("    1. Включи VPN с зарубежным сервером")
        print("    2. Если VPN есть, но не помогает — укажи прокси в .env:")
        print("       HTTPS_PROXY=socks5://127.0.0.1:PORT")
        print("    3. Пока Telegram недоступен, открой веб-версию:")
        print("       venv\\Scripts\\python.exe webapp.py   ->  http://127.0.0.1:8080")
        print("=" * 60 + "\n")
        raise SystemExit(1)

    proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .get_updates_connect_timeout(45)
        .connection_pool_size(1)
        .post_init(on_startup)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("voice", cmd_voice))
    app.add_handler(CommandHandler("ref", cmd_ref))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

    def _warm():
        if has_ref():
            warmup()

    threading.Thread(target=_warm, daemon=True).start()

    log.info("подключаюсь к Telegram%s", f" через прокси {proxy}" if proxy else "")
    try:
        app.run_polling(
            drop_pending_updates=True,
            bootstrap_retries=2,
            close_loop=False,
        )
    except OSError as e:
        raise SystemExit(f"Соединение с Telegram оборвалось: {e}\nПроверь VPN.")
    except KeyboardInterrupt:
        log.info("остановлено")
    finally:
        shutdown()


if __name__ == "__main__":
    main()