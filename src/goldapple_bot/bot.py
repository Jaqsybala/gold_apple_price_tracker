"""Telegram bot: track goldapple.kz product prices and notify on drops."""

from __future__ import annotations

import asyncio
import logging
import os
import re
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
from goldapple_bot.price_fetcher import extract_all_goldapple_kz_urls, fetch_price_kz

load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "3600"))
LIST_PAGE_SIZE = max(1, int(os.environ.get("LIST_PAGE_SIZE", "5")))

BTN_MY_LIST = "📋 Мой список"
BTN_HELP = "❓ Как пользоваться"
BTN_SEARCH = "🔍 Поиск в списке"
EMPTY_WATCH_LIST_TEXT = "Пока ничего не отслеживается. Пришли ссылку на товар."
CALLBACK_BAD_BUTTON = "Некорректная кнопка."
USER_DATA_AWAIT_SEARCH = "await_search_query"

SEARCH_PROMPT_TEXT = (
    "Напиши одним сообщением слова для поиска (через пробел).\n"
    "Покажу товары из твоего списка, где встречаются все эти слова."
)

HELP_TEXT = (
    "Пришли ссылку на товар с goldapple.kz — буду проверять цену и "
    "напишу, если она станет ниже.\n\n"
    "Кнопки внизу экрана — как на подписи: «📋 Мой список», «🔍 Поиск в списке», "
    "«❓ Как пользоваться». В списке и уведомлениях — «Открыть» и «Удалить».\n\n"
    "Команды (как в меню слева от поля ввода):\n"
    "/list — что отслеживается\n"
    "/search — режим поиска по списку\n"
    "/remove N — убрать N-й пункт (номер как в /list)\n\n"
    "Поиск: несколько слов через пробел — все должны встретиться в названии или ссылке. "
    "Если совпадений много — листай страницы.\n\n"
    "Можно прислать несколько ссылок в одном сообщении — обработаю по очереди.\n\n"
    "Если пришлёшь новую ссылку, пока ещё грузится предыдущая, она подождёт "
    "и обработается следом — не теряется."
)

BOT_DATA_LAST_SEARCH_TOKENS = "last_search_tokens"
BOT_DATA_CHAT_FETCH_LOCKS = "_chat_url_fetch_locks"
BTN_OPEN_PRODUCT = "🔗 Открыть"
BTN_DELETE_WATCH = "🗑 Удалить"


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_MY_LIST), KeyboardButton(BTN_SEARCH)],
            [KeyboardButton(BTN_HELP)],
        ],
        resize_keyboard=True,
    )


def _watch_list_line_compact(r: dict, position: int) -> str:
    title = (r.get("title") or "").strip()
    short = (title[:48] + "…") if len(title) > 48 else title
    extra = f" — {short}" if short else ""
    return f"{position}. {r['last_price']:,} ₸{extra}"


def _text_search_tokens(text: str) -> list[str]:
    return [t for t in re.split(r"\s+", text.strip()) if t]


def _watch_matches_tokens(row: dict, tokens: list[str]) -> bool:
    if not tokens:
        return False
    hay = " ".join(
        filter(
            None,
            [(row.get("title") or "").strip(), (row.get("url") or "").strip()],
        )
    ).casefold()
    return all(tok.casefold() in hay for tok in tokens)


def build_watch_list_page(
    rows: list[dict],
    page: int,
    *,
    list_heading: str | None = None,
    delete_callback: str = "delp",
    line_positions: dict[int, int] | None = None,
) -> tuple[str, InlineKeyboardMarkup, int]:
    """Текст, клавиатура и индекс страницы (0-based) после нормализации."""
    total = len(rows)
    pages = max(1, (total + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * LIST_PAGE_SIZE
    chunk = rows[start : start + LIST_PAGE_SIZE]

    heading = list_heading if list_heading is not None else "Отслеживание"
    header = f"{heading} — стр. {page + 1}/{pages}\n\n"
    body_parts: list[str] = []
    for i, r in enumerate(chunk):
        if line_positions is not None:
            pos = line_positions[int(r["id"])]
        else:
            pos = start + i + 1
        body_parts.append(_watch_list_line_compact(r, pos))
    body = "\n\n".join(body_parts)
    text = header + body + "\n\nНиже у каждой позиции — «Открыть» и «Удалить»."

    keyboard: list[list[InlineKeyboardButton]] = []
    pg_prefix = "srchpg" if delete_callback == "delps" else "listpg"
    for r in chunk:
        if delete_callback == "delps":
            del_cb = f"delps:{r['id']}:{page}"
        else:
            del_cb = f"delp:{r['id']}:{page}"
        keyboard.append(
            [
                InlineKeyboardButton(BTN_OPEN_PRODUCT, url=r["url"]),
                InlineKeyboardButton(BTN_DELETE_WATCH, callback_data=del_cb),
            ]
        )
    if pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton("◀️ Назад", callback_data=f"{pg_prefix}:{page - 1}")
            )
        noop = "srchpg:noop" if delete_callback == "delps" else "listpg:noop"
        nav.append(InlineKeyboardButton(f"· {page + 1}/{pages} ·", callback_data=noop))
        if page < pages - 1:
            nav.append(
                InlineKeyboardButton("Вперёд ▶️", callback_data=f"{pg_prefix}:{page + 1}")
            )
        keyboard.append(nav)

    if delete_callback == "delps":
        keyboard.append(
            [
                InlineKeyboardButton("🔍 Другой запрос", callback_data="nav:search"),
                InlineKeyboardButton("📋 Весь список", callback_data="nav:list"),
            ]
        )

    return text, InlineKeyboardMarkup(keyboard), page


_LISTPG_RE = re.compile(r"^listpg:(\d+)$")
_SRCHPG_RE = re.compile(r"^srchpg:(\d+)$")


def _global_line_positions(rows: list[dict]) -> dict[int, int]:
    return {int(r["id"]): i + 1 for i, r in enumerate(rows)}


def _format_search_heading(tokens: list[str], total_matches: int) -> str:
    q = " ".join(tokens)
    if len(q) > 40:
        q = q[:37] + "…"
    return f"Поиск «{q}» ({total_matches})"


async def send_watch_list_paginated(
    message,
    chat_id: int,
    page: int = 0,
    *,
    context: ContextTypes.DEFAULT_TYPE | None = None,
    rows: list[dict] | None = None,
    list_heading: str | None = None,
    delete_callback: str = "delp",
    line_positions: dict[int, int] | None = None,
) -> None:
    if rows is None:
        rows = db.list_watches_for_chat(chat_id)
        if context is not None:
            context.application.bot_data.setdefault(BOT_DATA_LAST_SEARCH_TOKENS, {}).pop(chat_id, None)
    if not rows:
        await message.reply_text(EMPTY_WATCH_LIST_TEXT, reply_markup=main_reply_keyboard())
        return
    text, kb, _ = build_watch_list_page(
        rows,
        page,
        list_heading=list_heading,
        delete_callback=delete_callback,
        line_positions=line_positions,
    )
    await message.reply_text(text, reply_markup=kb, disable_web_page_preview=True)


async def edit_watch_list_paginated(
    query,
    chat_id: int,
    page: int,
    *,
    rows: list[dict] | None = None,
    list_heading: str | None = None,
    delete_callback: str = "delp",
    line_positions: dict[int, int] | None = None,
) -> None:
    if rows is None:
        rows = db.list_watches_for_chat(chat_id)
    if not rows:
        await query.message.edit_text(EMPTY_WATCH_LIST_TEXT, reply_markup=None)
        return
    text, kb, _ = build_watch_list_page(
        rows,
        page,
        list_heading=list_heading,
        delete_callback=delete_callback,
        line_positions=line_positions,
    )
    await query.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)


def after_watch_inline(watch_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(BTN_MY_LIST, callback_data="nav:list")],
            [InlineKeyboardButton(BTN_DELETE_WATCH, callback_data=f"del:{watch_id}")],
        ]
    )


def invalid_input_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(BTN_MY_LIST, callback_data="nav:list"),
                InlineKeyboardButton("🔍 Поиск", callback_data="nav:search"),
            ],
            [InlineKeyboardButton(BTN_HELP, callback_data="nav:help")],
        ]
    )


async def reply_watch_list(
    message, chat_id: int, *, context: ContextTypes.DEFAULT_TYPE | None = None
) -> None:
    await send_watch_list_paginated(message, chat_id, page=0, context=context)


def _chat_fetch_lock(application, chat_id: int) -> asyncio.Lock:
    """Один запрос на чат: следующая ссылка ждёт, пока догрузится предыдущая."""
    locks: dict = application.bot_data.setdefault(BOT_DATA_CHAT_FETCH_LOCKS, {})
    if chat_id not in locks:
        locks[chat_id] = asyncio.Lock()
    return locks[chat_id]


async def handle_search_query(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    tokens = _text_search_tokens(text)
    if not tokens:
        await update.effective_message.reply_text(
            "Нужно хотя бы одно слово для поиска.",
            reply_markup=main_reply_keyboard(),
        )
        return
    chat_id = update.effective_chat.id
    all_rows = db.list_watches_for_chat(chat_id)
    matched = [r for r in all_rows if _watch_matches_tokens(r, tokens)]
    if matched:
        context.application.bot_data.setdefault(BOT_DATA_LAST_SEARCH_TOKENS, {})[
            chat_id
        ] = tokens
        heading = _format_search_heading(tokens, len(matched))
        line_positions = _global_line_positions(all_rows)
        await send_watch_list_paginated(
            update.effective_message,
            chat_id,
            page=0,
            context=context,
            rows=matched,
            list_heading=heading,
            delete_callback="delps",
            line_positions=line_positions,
        )
        return
    await update.effective_message.reply_text(
        "По этому запросу в отслеживаемом списке ничего не нашла.",
        reply_markup=main_reply_keyboard(),
    )


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Начать"),
            BotCommand("list", "Что отслеживается"),
            BotCommand("search", "Поиск по своему списку"),
            BotCommand("remove", "Убрать по номеру строки из /list"),
            BotCommand("help", "Как пользоваться"),
        ]
    )
    db.init_db()
    application.bot_data["_price_poll_task"] = asyncio.create_task(
        price_poll_loop(application)
    )
    logger.info("DB: PostgreSQL, poll every %s s", CHECK_INTERVAL_SECONDS)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, reply_markup=main_reply_keyboard())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, reply_markup=main_reply_keyboard())


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        await handle_search_query(update, context, " ".join(context.args))
        return
    context.user_data[USER_DATA_AWAIT_SEARCH] = True
    await update.effective_message.reply_text(
        SEARCH_PROMPT_TEXT, reply_markup=main_reply_keyboard()
    )


async def list_watches_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_watch_list(
        update.effective_message, update.effective_chat.id, context=context
    )


async def _callback_list_page_nav(query, page: int) -> None:
    await query.answer()
    try:
        await edit_watch_list_paginated(query, query.message.chat.id, page)
    except Exception:
        logger.exception("edit_watch_list_paginated")


async def _callback_search_page_nav(
    query, page: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    chat_id = query.message.chat.id
    tokens = context.application.bot_data.get(BOT_DATA_LAST_SEARCH_TOKENS, {}).get(chat_id)
    if not tokens:
        await query.answer("Сначала снова выполни поиск.", show_alert=True)
        return
    await query.answer()
    all_rows = db.list_watches_for_chat(chat_id)
    matched = [r for r in all_rows if _watch_matches_tokens(r, tokens)]
    if not matched:
        try:
            await query.message.edit_text(
                "По этому запросу в списке больше ничего нет.",
                reply_markup=None,
            )
        except Exception:
            logger.exception("edit_message search nav empty")
        context.application.bot_data.setdefault(BOT_DATA_LAST_SEARCH_TOKENS, {}).pop(
            chat_id, None
        )
        return
    pages = max(1, (len(matched) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    heading = _format_search_heading(tokens, len(matched))
    line_positions = _global_line_positions(all_rows)
    try:
        await edit_watch_list_paginated(
            query,
            chat_id,
            page,
            rows=matched,
            list_heading=heading,
            delete_callback="delps",
            line_positions=line_positions,
        )
    except Exception:
        logger.exception("edit_watch_list_paginated search page")


async def _callback_del_from_search(
    query, wid: int, page: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    chat_id = query.message.chat.id
    tokens = context.application.bot_data.get(BOT_DATA_LAST_SEARCH_TOKENS, {}).get(chat_id)
    if not db.delete_watch(chat_id, wid):
        await query.answer("Этой записи уже нет.", show_alert=True)
        return
    await query.answer("Убрала из отслеживания.")
    if not tokens:
        rows = db.list_watches_for_chat(chat_id)
        if not rows:
            try:
                await query.message.edit_text(EMPTY_WATCH_LIST_TEXT, reply_markup=None)
            except Exception:
                logger.exception("edit_message after delps empty")
            return
        try:
            await edit_watch_list_paginated(query, chat_id, 0)
        except Exception:
            logger.exception("edit_watch_list_paginated after delps")
        return
    matched = [
        r
        for r in db.list_watches_for_chat(chat_id)
        if _watch_matches_tokens(r, tokens)
    ]
    if not matched:
        try:
            await query.message.edit_text(
                "По этому запросу в списке больше ничего нет.",
                reply_markup=None,
            )
        except Exception:
            logger.exception("edit_message after delps no matches")
        context.application.bot_data.setdefault(BOT_DATA_LAST_SEARCH_TOKENS, {}).pop(
            chat_id, None
        )
        return
    pages = max(1, (len(matched) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    heading = _format_search_heading(tokens, len(matched))
    line_positions = _global_line_positions(db.list_watches_for_chat(chat_id))
    try:
        await edit_watch_list_paginated(
            query,
            chat_id,
            page,
            rows=matched,
            list_heading=heading,
            delete_callback="delps",
            line_positions=line_positions,
        )
    except Exception:
        logger.exception("edit_watch_list_paginated after delps")


async def _callback_del_alert_or_card(query, wid: int) -> None:
    ok = db.delete_watch(query.message.chat.id, wid)
    if ok:
        await query.answer("Убрала из отслеживания.")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            logger.exception("edit_message_reply_markup after delete")
    else:
        await query.answer("Этой записи уже нет.", show_alert=True)


async def _callback_del_from_list(query, data: str) -> None:
    parts = data.split(":")
    if len(parts) != 3:
        await query.answer(CALLBACK_BAD_BUTTON, show_alert=True)
        return
    try:
        wid = int(parts[1])
        page = int(parts[2])
    except ValueError:
        await query.answer(CALLBACK_BAD_BUTTON, show_alert=True)
        return
    chat_id = query.message.chat.id
    if not db.delete_watch(chat_id, wid):
        await query.answer("Этой записи уже нет.", show_alert=True)
        return
    await query.answer("Убрала из отслеживания.")
    rows = db.list_watches_for_chat(chat_id)
    if not rows:
        try:
            await query.message.edit_text(EMPTY_WATCH_LIST_TEXT, reply_markup=None)
        except Exception:
            logger.exception("edit_message after delp empty list")
        return
    pages = max(1, (len(rows) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = min(page, pages - 1)
    try:
        await edit_watch_list_paginated(query, chat_id, page)
    except Exception:
        logger.exception("edit_watch_list_paginated after delp")


async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text(
            "Например: /remove 2 — убирает вторую строку из списка /list."
        )
        return
    try:
        index = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Номер должен быть целым числом.")
        return
    rows = db.list_watches_for_chat(update.effective_chat.id)
    if not rows:
        await update.effective_message.reply_text("Список пуст — нечего удалять.")
        return
    if index < 1 or index > len(rows):
        await update.effective_message.reply_text(
            f"В списке сейчас {len(rows)} поз. Укажи число от 1 до {len(rows)}."
        )
        return
    wid = int(rows[index - 1]["id"])
    if db.delete_watch(update.effective_chat.id, wid):
        await update.effective_message.reply_text("Убрал из отслеживания.")
    else:
        await update.effective_message.reply_text("Такой записи нет.")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = query.data or ""

    if data == "listpg:noop":
        await query.answer()
        return
    if data == "srchpg:noop":
        await query.answer()
        return

    m = _SRCHPG_RE.match(data)
    if m:
        await _callback_search_page_nav(query, int(m.group(1)), context)
        return

    m = _LISTPG_RE.match(data)
    if m:
        await _callback_list_page_nav(query, int(m.group(1)))
        return

    if data.startswith("delp:"):
        await _callback_del_from_list(query, data)
        return

    if data.startswith("delps:"):
        parts = data.split(":")
        if len(parts) == 2:
            try:
                wid = int(parts[1])
            except ValueError:
                await query.answer(CALLBACK_BAD_BUTTON, show_alert=True)
                return
            page = 0
        elif len(parts) == 3:
            try:
                wid = int(parts[1])
                page = int(parts[2])
            except ValueError:
                await query.answer(CALLBACK_BAD_BUTTON, show_alert=True)
                return
        else:
            await query.answer(CALLBACK_BAD_BUTTON, show_alert=True)
            return
        await _callback_del_from_search(query, wid, page, context)
        return

    if data.startswith("del:"):
        try:
            wid = int(data.split(":", 1)[1])
        except ValueError:
            await query.answer(CALLBACK_BAD_BUTTON, show_alert=True)
            return
        await _callback_del_alert_or_card(query, wid)
        return

    if data == "nav:list":
        await query.answer()
        await reply_watch_list(query.message, query.message.chat.id, context=context)
        return

    if data == "nav:search":
        await query.answer()
        context.user_data[USER_DATA_AWAIT_SEARCH] = True
        await query.message.reply_text(
            SEARCH_PROMPT_TEXT, reply_markup=main_reply_keyboard()
        )
        return

    if data == "nav:help":
        await query.answer()
        await query.message.reply_text(HELP_TEXT, reply_markup=main_reply_keyboard())
        return

    await query.answer()


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip()

    if text == BTN_MY_LIST:
        await reply_watch_list(
            update.effective_message, update.effective_chat.id, context=context
        )
        return
    if text == BTN_HELP:
        await update.effective_message.reply_text(HELP_TEXT, reply_markup=main_reply_keyboard())
        return
    if text == BTN_SEARCH:
        context.user_data[USER_DATA_AWAIT_SEARCH] = True
        await update.effective_message.reply_text(
            SEARCH_PROMPT_TEXT, reply_markup=main_reply_keyboard()
        )
        return

    urls = extract_all_goldapple_kz_urls(text)
    if urls:
        context.user_data.pop(USER_DATA_AWAIT_SEARCH, None)
        chat_id = update.effective_chat.id
        msg = update.effective_message
        lock = _chat_fetch_lock(context.application, chat_id)
        # Сообщение ниже срабатывает только при concurrent_updates=True: иначе второй on_text
        # не стартует, пока первый не закончил весь fetch (см. Application.builder).
        if lock.locked():
            await msg.reply_text(
                "Сейчас уже идёт проверка цены по другому твоему сообщению. "
                "Это не потерялось: как только закончу с тем запросом, сразу перейду к этому."
            )
        async with lock:
            n = len(urls)
            if n == 1:
                await msg.reply_text("Проверяю цену...")
            else:
                await msg.reply_text(
                    f"В сообщении {n} ссылок — обрабатываю по одной, в том же порядке. "
                    f"На каждую нужно открыть страницу, так что суммарно может занять несколько минут. "
                    f"Результат по каждой пришлю отдельным сообщением."
                )
            for i, url in enumerate(urls, 1):
                head = f"Ссылка {i} из {n}\n" if n > 1 else ""
                price, title, err = await fetch_price_kz(url)
                if err or price is None:
                    await msg.reply_text(
                        f"{head}{err or 'Не удалось получить цену.'}\n{url}"
                    )
                    continue

                created, wid = db.add_watch(chat_id, url, price, title)
                if wid is None:
                    await msg.reply_text(
                        f"{head}Не удалось сохранить отслеживание.\n{url}"
                    )
                    continue

                if not created:
                    await msg.reply_text(
                        f"{head}Уже в списке отслеживания. Сейчас на сайте: {price:,} ₸\n{url}",
                        reply_markup=after_watch_inline(wid),
                    )
                    continue

                await msg.reply_text(
                    f"{head}Добавила в отслеживание. Текущая цена: {price:,} ₸\n"
                    f"Напишу, если станет дешевле.\n{url}",
                    reply_markup=after_watch_inline(wid),
                )
        return

    if context.user_data.pop(USER_DATA_AWAIT_SEARCH, None):
        await handle_search_query(update, context, text)
        return

    if _text_search_tokens(text):
        await handle_search_query(update, context, text)
        return

    await update.effective_message.reply_text(
        "Нужна ссылка вида https://goldapple.kz/… "
        "или слова для поиска. Кнопка «Поиск в списке» подскажет формат.",
        reply_markup=invalid_input_inline(),
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
                [InlineKeyboardButton(BTN_OPEN_PRODUCT, url=row["url"])],
                [InlineKeyboardButton(BTN_DELETE_WATCH, callback_data=f"del:{wid}")],
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
    db.update_watch_price(int(row["id"]), price, title)


async def price_poll_loop(application: Application) -> None:
    await asyncio.sleep(15)
    bot = application.bot
    while True:
        try:
            rows = db.all_watches()
            for row in rows:
                await _poll_one_watch(bot, row)
        except Exception:
            logger.exception("price_poll_loop")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Задайте переменную окружения TELEGRAM_BOT_TOKEN")
    try:
        db.require_database_url()
    except RuntimeError as e:
        raise SystemExit(str(e)) from e

    application = (
        Application.builder()
        .token(token)
        .concurrent_updates(True)
        .post_init(post_init)
        .build()
    )
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("list", list_watches_cmd))
    application.add_handler(CommandHandler("search", search_cmd))
    application.add_handler(CommandHandler("remove", remove_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
