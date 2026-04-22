"""Fetch product price from goldapple.kz (HTTP first, Playwright fallback)."""

from __future__ import annotations

import asyncio
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

from playwright.async_api import async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeout

ALLOWED_NETLOC = "goldapple.kz"
META_PRICE_RE = re.compile(
    r'<meta\s+itemprop="price"\s+content="(\d+)"',
    re.IGNORECASE,
)
TITLE_RE = re.compile(r"<title[^>]*>([^<]*)</title>", re.IGNORECASE)


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


def _http_get_html(url: str, *, timeout_s: float = 30.0) -> str | None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except (urllib.error.URLError, TimeoutError):
        return None


async def fetch_price_kz(url: str, *, timeout_ms: int = 90_000) -> tuple[int | None, str | None, str | None]:
    """
    Load product page and return (price_kzt, title, error).

    Current sale price is the minimum of all positive schema.org price metas
    (strikethrough RRP and current price are both present when discounted).
    """
    parsed = urlparse(url)
    if parsed.netloc.lower() != ALLOWED_NETLOC or not url.lower().startswith("https://"):
        return None, None, "Разрешены только ссылки https://goldapple.kz/..."

    timeout_s = max(timeout_ms / 1000.0, 5.0)
    # HTTP + Playwright подряд; без верхней границы зависание держит lock в боте и блокирует чат.
    overall_cap = max(120.0, 2.0 * timeout_s + 45.0)

    async def _run() -> tuple[int | None, str | None, str | None]:
        html = await asyncio.to_thread(_http_get_html, url, timeout_s=timeout_s)
        if html:
            prices = list(dict.fromkeys(_prices_from_meta_html(html)))
            if prices:
                tm = TITLE_RE.search(html)
                title = (tm.group(1).strip() if tm else None) or None
                return min(prices), title, None

        async with async_playwright() as p:
            launch_timeout = min(timeout_ms, 120_000)
            browser = None
            for attempt in range(3):
                try:
                    browser = await p.chromium.launch(
                        headless=True,
                        timeout=launch_timeout,
                    )
                    break
                except Exception as e:
                    retryable = type(e).__name__ == "TargetClosedError" or (
                        "Target page, context or browser has been closed" in str(e)
                    )
                    if retryable and attempt < 2:
                        await asyncio.sleep(1.0 * (attempt + 1))
                        continue
                    return None, None, f"Ошибка: {e}"
            assert browser is not None
            try:
                page = await browser.new_page()
                try:
                    # networkidle на тяжёлых витринах часто не наступает → зависание до ручного стопа.
                    await page.goto(url, wait_until="load", timeout=timeout_ms)
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
                    html2 = await page.content()
                    prices = list(dict.fromkeys(_prices_from_meta_html(html2)))

                if not prices:
                    return None, title, "Не удалось найти цену на странице (сайт мог измениться)"

                return min(prices), title, None
            except Exception as e:
                return None, None, f"Ошибка: {e}"
            finally:
                await browser.close()

    try:
        return await asyncio.wait_for(_run(), timeout=overall_cap)
    except asyncio.TimeoutError:
        return (
            None,
            None,
            "Сайт не ответил в разумный срок — запрос прерван. "
            "Попробуй снова или перезапусти бота, если зависание повторяется.",
        )
