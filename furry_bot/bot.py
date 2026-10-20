"""Телеграм-бот: присылаешь селфи — получаешь его фури-версию."""

import logging
import os

from openai import AsyncOpenAI, OpenAIError
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from furry import furrify

log = logging.getLogger("furry_bot")

HELLO = (
    "Привет! Пришли мне селфи — сделаю из тебя фури 🦊\n"
    "Можно указать зверя в подписи к фото: «волк», «кот», «лиса»…"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELLO)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if msg.photo:
        file = await msg.photo[-1].get_file()  # самое большое разрешение
    elif msg.document and (msg.document.mime_type or "").startswith("image/"):
        file = await msg.document.get_file()
    else:
        await msg.reply_text("Это не похоже на фото. Пришли селфи картинкой.")
        return

    status = await msg.reply_text("Отращиваю шерсть… это займёт до минуты ⏳")
    await context.bot.send_chat_action(msg.chat_id, ChatAction.UPLOAD_PHOTO)
    photo = bytes(await file.download_as_bytearray())
    species = (msg.caption or "").strip()[:40] or None

    try:
        result = await furrify(photo, context.bot_data["openai"], species)
    except OpenAIError as e:
        log.warning("openai error: %s", e)
        await status.edit_text(
            "Не получилось 😿 Попробуй другое фото — лицо крупно, хороший свет."
        )
        return

    await msg.reply_photo(result, caption="Вот твоя фури-версия 🐾")
    await status.delete()


async def on_other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELLO)


def build_app(token: str, openai_client: AsyncOpenAI) -> Application:
    app = Application.builder().token(token).concurrent_updates(True).build()
    app.bot_data["openai"] = openai_client
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_photo))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_other))
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # иначе в логе светится токен
    app = build_app(os.environ["TELEGRAM_BOT_TOKEN"], AsyncOpenAI())
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
