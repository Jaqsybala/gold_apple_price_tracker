# Бот цен goldapple.kz

Telegram-бот принимает ссылку на товар с [goldapple.kz](https://goldapple.kz), периодически проверяет цену и пишет в чат, если она стала ниже последней сохранённой.

## Структура проекта

```
Python/
├── pyproject.toml          # зависимости и метаданные пакета
├── README.md
├── Dockerfile
├── .env.example
├── data/                   # SQLite по умолчанию (data/watches.db)
└── src/
    └── goldapple_bot/      # код приложения
        ├── __main__.py     # точка входа: python -m goldapple_bot
        ├── bot.py
        ├── db.py
        └── price_fetcher.py
```

## Что нужно

- Python 3.10+
- Токен бота от [@BotFather](https://t.me/BotFather)

## Установка

Из корня проекта (папка `Python`):

```bash
pip install -e .
playwright install chromium
```

## Запуск

Рабочая директория — корень проекта (чтобы находились `data/` и `.env`).

**Windows (PowerShell):**

```powershell
$env:TELEGRAM_BOT_TOKEN="ваш_токен"
python -m goldapple_bot
```

Либо после установки пакета:

```powershell
goldapple-bot
```

**Linux/macOS:**

```bash
export TELEGRAM_BOT_TOKEN="ваш_токен"
python -m goldapple_bot
```

Опционально:

- `CHECK_INTERVAL_SECONDS` — пауза между полными циклами проверки (по умолчанию `3600`, раз в час).
- `DB_PATH` — путь к файлу SQLite (по умолчанию `data/watches.db` относительно текущей директории).

Можно положить токен в файл `.env` в корне проекта:

```
TELEGRAM_BOT_TOKEN=ваш_токен
```

## Docker

```bash
docker build -t goldapple-bot .
docker run --rm -e TELEGRAM_BOT_TOKEN="ваш_токен" -v goldapple-data:/app/data goldapple-bot
```

## Команды в Telegram

- `/start` — подсказка
- `/list` — список отслеживаемых товаров
- `/remove N` — убрать запись с номером `N` из `/list`
- Любое сообщение со ссылкой `https://goldapple.kz/...` — добавить отслеживание

## Как определяется цена

Берётся минимальное положительное значение из разметки `meta itemprop="price"` (на странице обычно есть и зачёркнутая, и актуальная цена при скидке).

Если сайт изменит вёрстку, парсер может потребовать правки в `src/goldapple_bot/price_fetcher.py`.

## Ошибка `database is locked`

Закройте файл `data/watches.db` во внешних программах (DB Browser, второй запущенный экземпляр бота). Если папка проекта на **OneDrive** / в облаке, лучше перенесите проект или задайте `DB_PATH` на локальный диск. В коде включены ожидание блокировки (до ~30 с) и режим WAL для SQLite.

## Git Bash на Windows

Токен задаётся так: `export TELEGRAM_BOT_TOKEN="..."` (не `env:...`, это синтаксис PowerShell).
