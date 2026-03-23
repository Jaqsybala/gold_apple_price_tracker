"""Telegram bot: track goldapple.kz product prices and notify on drops."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from . import db
from .price_fetcher import fetch_price_kz, normalize_goldapple_kz_url

load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DB_PATH", "data/watches.db"))
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "3600"))


async def post_init(application: Application) -> None:
    db.init_db(DB_PATH)
    application.bot_data["_price_poll_task"] = asyncio.create_task(
        price_poll_loop(application)
    )
    logger.info("DB ready at %s, poll every %s s", DB_PATH.resolve(), CHECK_INTERVAL_SECONDS)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Пришли ссылку на товар с goldapple.kz — буду проверять цену и "
        "напишу, если она станет ниже.\n\n"
        "Команды:\n"
        "/list — что отслеживается\n"
        "/remove N — убрать по номеру из /list"
    )


async def list_watches_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    rows = db.list_watches_for_chat(DB_PATH, chat.id)
    if not rows:
        await update.effective_message.reply_text("Пока ничего не отслеживается. Пришли ссылку на товар.")
        return
    lines = []
    for r in rows:
        title = (r.get("title") or "").strip()
        short = (title[:50] + "…") if len(title) > 50 else title
        extra = f" — {short}" if short else ""
        lines.append(f"{r['id']}. {r['last_price']:,} ₸{extra}\n{r['url']}")
    await update.effective_message.reply_text("\n\n".join(lines))


async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Например: /remove 2 (номер из /list)")
        return
    try:
        wid = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Номер должен быть числом.")
        return
    if db.delete_watch(DB_PATH, update.effective_chat.id, wid):
        await update.effective_message.reply_text("Убрал из отслеживания.")
    else:
        await update.effective_message.reply_text("Такой записи нет.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip()
    url = normalize_goldapple_kz_url(text)
    if not url:
        await update.effective_message.reply_text(
            "Нужна ссылка вида https://goldapple.kz/..."
        )
        return

    await update.effective_message.reply_text("Смотрю цену…")
    price, title, err = await fetch_price_kz(url)
    if err or price is None:
        await update.effective_message.reply_text(err or "Не удалось получить цену.")
        return

    created, _ = db.add_watch(DB_PATH, update.effective_chat.id, url, price, title)
    if not created:
        await update.effective_message.reply_text(
            f"Эта ссылка уже в списке. Сейчас на сайте: {price:,} ₸\n/list — все отслеживания"
        )
        return

    await update.effective_message.reply_text(
        f"Отслеживаю. Текущая цена: {price:,} ₸\n"
        f"Напишу, если станет дешевле.\n{url}"
    )


async def price_poll_loop(application: Application) -> None:
    await asyncio.sleep(15)
    bot = application.bot
    while True:
        try:
            rows = db.all_watches(DB_PATH)
            for row in rows:
                price, title, err = await fetch_price_kz(row["url"])
                if err or price is None:
                    logger.warning("poll skip id=%s url=%s: %s", row["id"], row["url"], err)
                    continue
                prev = int(row["last_price"])
                chat_id = int(row["chat_id"])
                if price < prev:
                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"Цена снизилась: было {prev:,} ₸, стало {price:,} ₸\n"
                                f"{row['url']}"
                            ),
                        )
                    except Exception:
                        logger.exception("send_message chat_id=%s", chat_id)
                db.update_watch_price(DB_PATH, int(row["id"]), price, title)
        except Exception:
            logger.exception("price_poll_loop")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Задайте переменную окружения TELEGRAM_BOT_TOKEN")

    application = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", list_watches_cmd))
    application.add_handler(CommandHandler("remove", remove_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
