"""Telegram bot: track goldapple.kz product prices and notify on drops."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Поддержка запуска как `python src/goldapple_bot/bot.py` (папка `src` в sys.path).
if __name__ == "__main__":
    _src_root = Path(__file__).resolve().parent.parent
    _src_s = str(_src_root)
    if _src_s not in sys.path:
        sys.path.insert(0, _src_s)

from dotenv import load_dotenv
from telegram import Bot, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from goldapple_bot import db
from goldapple_bot.price_fetcher import fetch_price_kz, normalize_goldapple_kz_url

load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DB_PATH", "data/watches.db"))
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "3600"))

BTN_MY_LIST = "📋 Мой список"
BTN_HELP = "❓ Как пользоваться"

HELP_TEXT = (
    "Пришли ссылку на товар с goldapple.kz — буду проверять цену и "
    "напишу, если она станет ниже.\n\n"
    "Команды:\n"
    "/list — что отслеживается\n"
    "/remove N — убрать по номеру из списка\n\n"
    "Можно пользоваться кнопками внизу экрана."
)


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_MY_LIST), KeyboardButton(BTN_HELP)]],
        resize_keyboard=True,
    )


def _watch_list_lines(rows: list[dict]) -> list[str]:
    lines = []
    for r in rows:
        title = (r.get("title") or "").strip()
        short = (title[:50] + "…") if len(title) > 50 else title
        extra = f" — {short}" if short else ""
        lines.append(f"{r['id']}. {r['last_price']:,} ₸{extra}\n{r['url']}")
    return lines


def list_inline_keyboard(rows: list[dict]) -> InlineKeyboardMarkup:
    keyboard = []
    for r in rows:
        keyboard.append(
            [
                InlineKeyboardButton("Открыть на сайте", url=r["url"]),
                InlineKeyboardButton("Удалить", callback_data=f"del:{r['id']}"),
            ]
        )
    return InlineKeyboardMarkup(keyboard)


def after_watch_inline(watch_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(BTN_MY_LIST, callback_data="nav:list")],
            [InlineKeyboardButton("Удалить это", callback_data=f"del:{watch_id}")],
        ]
    )


def invalid_input_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(BTN_HELP, callback_data="nav:help")]])


async def reply_watch_list(message, chat_id: int) -> None:
    rows = db.list_watches_for_chat(DB_PATH, chat_id)
    if not rows:
        await message.reply_text(
            "Пока ничего не отслеживается. Пришли ссылку на товар.",
            reply_markup=main_reply_keyboard(),
        )
        return
    body = "\n\n".join(_watch_list_lines(rows))
    await message.reply_text(body, reply_markup=list_inline_keyboard(rows))


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Начать"),
            BotCommand("list", "Что отслеживается"),
            BotCommand("remove", "Убрать по номеру из списка"),
            BotCommand("help", "Как пользоваться"),
        ]
    )
    db.init_db(DB_PATH)
    application.bot_data["_price_poll_task"] = asyncio.create_task(
        price_poll_loop(application)
    )
    logger.info("DB ready at %s, poll every %s s", DB_PATH.resolve(), CHECK_INTERVAL_SECONDS)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, reply_markup=main_reply_keyboard())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, reply_markup=main_reply_keyboard())


async def list_watches_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_watch_list(update.effective_message, update.effective_chat.id)


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


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = query.data or ""

    if data.startswith("del:"):
        try:
            wid = int(data.split(":", 1)[1])
        except ValueError:
            await query.answer("Некорректная кнопка.", show_alert=True)
            return
        ok = db.delete_watch(DB_PATH, query.message.chat.id, wid)
        if ok:
            await query.answer("Убрала из отслеживания.")
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                logger.exception("edit_message_reply_markup after delete")
        else:
            await query.answer("Этой записи уже нет.", show_alert=True)
        return

    if data == "nav:list":
        await query.answer()
        await reply_watch_list(query.message, query.message.chat.id)
        return

    if data == "nav:help":
        await query.answer()
        await query.message.reply_text(HELP_TEXT, reply_markup=main_reply_keyboard())
        return

    await query.answer()


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip()

    if text == BTN_MY_LIST:
        await reply_watch_list(update.effective_message, update.effective_chat.id)
        return
    if text == BTN_HELP:
        await update.effective_message.reply_text(HELP_TEXT, reply_markup=main_reply_keyboard())
        return

    url = normalize_goldapple_kz_url(text)
    if not url:
        await update.effective_message.reply_text(
            "Нужна ссылка вида https://goldapple.kz/...",
            reply_markup=invalid_input_inline(),
        )
        return

    await update.effective_message.reply_text("Смотрю цену…")
    price, title, err = await fetch_price_kz(url)
    if err or price is None:
        await update.effective_message.reply_text(err or "Не удалось получить цену.")
        return

    created, wid = db.add_watch(DB_PATH, update.effective_chat.id, url, price, title)
    if wid is None:
        await update.effective_message.reply_text("Не удалось сохранить отслеживание.")
        return

    if not created:
        await update.effective_message.reply_text(
            f"Эта ссылка уже в списке. Сейчас на сайте: {price:,} ₸",
            reply_markup=after_watch_inline(wid),
        )
        return

    await update.effective_message.reply_text(
        f"Отслеживаю. Текущая цена: {price:,} ₸\n"
        f"Напишу, если станет дешевле.\n{url}",
        reply_markup=after_watch_inline(wid),
    )


async def _poll_one_watch(bot: Bot, row: dict) -> None:
    price, title, err = await fetch_price_kz(row["url"])
    if err or price is None:
        logger.warning("poll skip id=%s url=%s: %s", row["id"], row["url"], err)
        return
    prev = int(row["last_price"])
    chat_id = int(row["chat_id"])
    if price < prev:
        wid = int(row["id"])
        alert_kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Открыть товар", url=row["url"])],
                [InlineKeyboardButton("Убрать из отслеживания", callback_data=f"del:{wid}")],
            ]
        )
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"Цена снизилась: было {prev:,} ₸, стало {price:,} ₸\n"
                    f"{row['url']}"
                ),
                reply_markup=alert_kb,
            )
        except Exception:
            logger.exception("send_message chat_id=%s", chat_id)
    db.update_watch_price(DB_PATH, int(row["id"]), price, title)


async def price_poll_loop(application: Application) -> None:
    await asyncio.sleep(15)
    bot = application.bot
    while True:
        try:
            rows = db.all_watches(DB_PATH)
            for row in rows:
                await _poll_one_watch(bot, row)
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
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("list", list_watches_cmd))
    application.add_handler(CommandHandler("remove", remove_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
