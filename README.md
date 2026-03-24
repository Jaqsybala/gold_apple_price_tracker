# Бот цен goldapple.kz

Telegram-бот принимает ссылку на товар с [goldapple.kz](https://goldapple.kz), периодически проверяет цену и пишет в чат, если она стала ниже последней сохранённой.

Данные хранятся в **PostgreSQL** (переменная **`DATABASE_URL`**).

## Структура проекта

```
Python/
├── pyproject.toml
├── README.md
├── Dockerfile
├── docker-compose.yml      # Postgres + бот одной командой
├── .env.example
└── src/
    └── goldapple_bot/
        ├── __main__.py     # точка входа: python -m goldapple_bot
        ├── bot.py
        ├── db.py
        └── price_fetcher.py
```

## Что нужно

- Python 3.10+
- **PostgreSQL** (локально, Docker или облако — Heroku Postgres и т.п.)
- Токен бота от [@BotFather](https://t.me/BotFather)

## Установка

Из корня проекта:

```bash
pip install -e .
playwright install chromium
```

## Переменные окружения

| Переменная | Обязательно | Описание |
|------------|-------------|----------|
| `TELEGRAM_BOT_TOKEN` | да | Токен бота |
| `DATABASE_URL` | да | URI PostgreSQL: `postgresql://USER:PASSWORD@HOST:PORT/DBNAME`. Префикс `postgres://` (Heroku) поддерживается. |
| `CHECK_INTERVAL_SECONDS` | нет | Интервал полного цикла проверки цен (по умолчанию `3600`). |
| `LIST_PAGE_SIZE` | нет | Сколько товаров на одной странице в `/list` (по умолчанию `5`). |

Пример `.env` в корне проекта — см. [`.env.example`](.env.example).

## Запуск локально (Python)

Подними Postgres (см. ниже Docker Compose или свой инстанс), затем:

**Windows (PowerShell):**

```powershell
$env:TELEGRAM_BOT_TOKEN="ваш_токен"
$env:DATABASE_URL="postgresql://USER:PASSWORD@localhost:5432/DBNAME"
python -m goldapple_bot
```

**Linux/macOS / Git Bash:**

```bash
export TELEGRAM_BOT_TOKEN="ваш_токен"
export DATABASE_URL="postgresql://USER:PASSWORD@localhost:5432/DBNAME"
python -m goldapple_bot
```

Подставьте те же `USER`, `PASSWORD` и `DBNAME`, что в `.env` для Postgres (см. [`.env.example`](.env.example)).

Либо после `pip install -e .`: `goldapple-bot` (те же переменные окружения).

## Docker Compose (Postgres + бот)

Скопируй [`.env.example`](.env.example) в `.env` и задай **`TELEGRAM_BOT_TOKEN`**, **`POSTGRES_PASSWORD`** (и при желании `POSTGRES_USER` / `POSTGRES_DB`). Пароль не должен попадать в репозиторий.

Запуск:

```bash
docker compose up --build
```

Postgres доступен на `localhost:5432` с учётными данными из `.env`. Данные в томе `pgdata`.

## Только образ бота (свой Postgres)

```bash
docker build -t goldapple-bot .
docker run --rm \
  -e TELEGRAM_BOT_TOKEN="ваш_токен" \
  -e DATABASE_URL="postgresql://USER:PASSWORD@host:5432/DBNAME" \
  goldapple-bot
```

## Команды в Telegram

- `/start` — подсказка
- `/list` — список отслеживаемых товаров
- `/remove N` — убрать запись с номером `N` из `/list`
- Любое сообщение со ссылкой `https://goldapple.kz/...` — добавить отслеживание

## Как определяется цена

Берётся минимальное положительное значение из разметки `meta itemprop="price"` (на странице обычно есть и зачёркнутая, и актуальная цена при скидке).

Если сайт изменит вёрстку, парсер может потребовать правки в `src/goldapple_bot/price_fetcher.py`.

## Git Bash на Windows

Токен: `export TELEGRAM_BOT_TOKEN="..."` (не `env:...`, это синтаксис PowerShell).
