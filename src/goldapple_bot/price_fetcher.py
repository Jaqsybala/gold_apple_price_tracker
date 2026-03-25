"""Fetch product price from goldapple.kz using Playwright."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from playwright.async_api import async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeout

ALLOWED_NETLOC = "goldapple.kz"
META_PRICE_RE = re.compile(
    r'<meta\s+itemprop="price"\s+content="(\d+)"',
    re.IGNORECASE,
)


# Останавливаемся перед следующей ссылкой на тот же домен (часто вставляют подряд без пробела).
_GA_URL_RE = re.compile(
    r"https?://goldapple\.kz/.+?(?=https?://goldapple\.kz|\s|$)",
    re.IGNORECASE,
)


def extract_all_goldapple_kz_urls(text: str) -> list[str]:
    """Все уникальные https://goldapple.kz/... из текста, порядок появления."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _GA_URL_RE.finditer(text):
        raw = m.group(0).rstrip(").,;]")
        parsed = urlparse(raw)
        if parsed.netloc.lower() != ALLOWED_NETLOC or not parsed.path or parsed.path == "/":
            continue
        norm = f"https://{ALLOWED_NETLOC}{parsed.path}"
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def normalize_goldapple_kz_url(text: str) -> str | None:
    """Первая ссылка goldapple.kz из текста, или None."""
    urls = extract_all_goldapple_kz_urls(text)
    return urls[0] if urls else None


def _prices_from_meta_html(html: str) -> list[int]:
    found = [int(x) for x in META_PRICE_RE.findall(html)]
    return [n for n in found if n > 0]


async def fetch_price_kz(url: str, *, timeout_ms: int = 90_000) -> tuple[int | None, str | None, str | None]:
    """
    Load product page and return (price_kzt, title, error).

    Current sale price is the minimum of all positive schema.org price metas
    (strikethrough RRP and current price are both present when discounted).
    """
    parsed = urlparse(url)
    if parsed.netloc.lower() != ALLOWED_NETLOC or not url.lower().startswith("https://"):
        return None, None, "Разрешены только ссылки https://goldapple.kz/..."

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except PlaywrightTimeout:
                return None, None, "Таймаут загрузки страницы"
            await page.wait_for_timeout(2500)

            title = (await page.title()).strip() or None

            prices = await page.evaluate(
                """() => {
                  const metas = [...document.querySelectorAll('meta[itemprop="price"]')];
                  const nums = metas
                    .map(m => parseInt(m.getAttribute('content') || '0', 10))
                    .filter(n => n > 0);
                  return [...new Set(nums)];
                }"""
            )

            if not prices:
                html = await page.content()
                prices = list(dict.fromkeys(_prices_from_meta_html(html)))

            if not prices:
                return None, title, "Не удалось найти цену на странице (сайт мог измениться)"

            return min(prices), title, None
        except Exception as e:
            return None, None, f"Ошибка: {e}"
        finally:
            await browser.close()
