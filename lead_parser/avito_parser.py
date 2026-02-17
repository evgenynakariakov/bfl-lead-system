"""
parser/avito_parser.py — Парсер Авито с антидетектом.

Использует Playwright (headless Chromium) для обхода JS-защиты Авито.
Парсит объявления по поисковым запросам, передаёт каждый лид через scorer.

Запуск:
    python -m parser.avito_parser
    
Или из кода:
    runner = AvitoParserRunner(db)
    await runner.run_all_cities()
"""
import asyncio
import random
import re
from datetime import datetime
from typing import Optional

from playwright.async_api import async_playwright, Page, BrowserContext
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import config
from lead_parser.scorer import score_lead


# ─────────────────────────────────────────────────────────────
#  Поисковые запросы (что ищем на Авито)
# ─────────────────────────────────────────────────────────────
# Ключевая идея: ищем НЕ "банкротство", а ПОВЕДЕНИЕ должника —
# он продаёт имущество. Объединяем категорию + сигнал стресса.

SEARCH_QUERIES = [
    # Авто — самая ценная категория
    "продам машину срочно",
    "продам авто нужны деньги",
    "продам автомобиль торг",
    # Электроника
    "продам iphone срочно",
    "продам ноутбук срочно нужны деньги",
    "продам телефон срочно торг",
    # Общее
    "срочная продажа долги",
    "продам вынужден расстаться",
    "снизил цену срочно",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


class AvitoScraper:
    """Низкоуровневый класс: открывает страницу, парсит HTML."""

    def __init__(self, context: BrowserContext):
        self.context = context
        self._page: Optional[Page] = None

    async def __aenter__(self):
        self._page = await self.context.new_page()
        # Скрываем webdriver fingerprint
        await self._page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['ru-RU', 'ru', 'en-US', 'en']
            });
        """)
        return self

    async def __aexit__(self, *args):
        if self._page:
            await self._page.close()

    async def _human_delay(self):
        """Случайная пауза как у человека."""
        await asyncio.sleep(
            random.uniform(config.AVITO_DELAY_MIN, config.AVITO_DELAY_MAX)
        )

    async def search_listings(self, city: str, query: str, page_num: int = 1) -> list[dict]:
        """
        Получить список объявлений с одной страницы поиска.

        URL формат: https://www.avito.ru/{city}?q={query}&p={page}
        """
        url = (
            f"https://www.avito.ru/{city}"
            f"?q={query.replace(' ', '+')}"
            f"&s=104"        # сортировка: сначала новые
            f"&p={page_num}"
        )

        logger.info(f"🔍 Парсим: {city} | '{query}' | стр. {page_num}")

        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await self._human_delay()

            # Проверка на капчу
            if await self._page.query_selector("[class*='captcha']"):
                logger.warning("⚠️ Обнаружена капча! Ждём 30 секунд...")
                await asyncio.sleep(30)
                return []

            # Скролл страницы как человек
            await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await asyncio.sleep(random.uniform(1, 2))
            await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(random.uniform(1, 2))

            # Ищем карточки объявлений
            listings = []
            cards = await self._page.query_selector_all("[data-marker='item']")

            logger.info(f"   Найдено карточек: {len(cards)}")

            for card in cards:
                listing = await self._parse_card(card)
                if listing:
                    listings.append(listing)

            return listings

        except Exception as e:
            logger.error(f"Ошибка при парсинге {url}: {e}")
            return []

    async def _parse_card(self, card) -> Optional[dict]:
        """Извлечь данные из одной карточки объявления."""
        try:
            # ID объявления
            avito_id = await card.get_attribute("data-item-id")
            if not avito_id:
                return None

            # Заголовок
            title_el = await card.query_selector("[itemprop='name']")
            title = (await title_el.inner_text()).strip() if title_el else ""

            # Ссылка
            link_el = await card.query_selector("a[data-marker='item-title']")
            href = await link_el.get_attribute("href") if link_el else ""
            url = f"https://www.avito.ru{href}" if href else ""

            # Цена
            price, price_old = await self._extract_prices(card)

            # Описание (короткое, с карточки)
            desc_el = await card.query_selector("[class*='description']")
            description = (await desc_el.inner_text()).strip() if desc_el else ""

            # Город/район
            geo_el = await card.query_selector("[data-marker='item-address']")
            location = (await geo_el.inner_text()).strip() if geo_el else ""

            # Категория (из хлебных крошек на карточке)
            cat_el = await card.query_selector("[class*='category']")
            category = (await cat_el.inner_text()).strip() if cat_el else ""

            # Продавец
            seller_el = await card.query_selector("[data-marker='seller-link']")
            seller = (await seller_el.inner_text()).strip() if seller_el else ""

            return {
                "avito_id":          avito_id,
                "avito_url":         url,
                "ad_title":          title,
                "ad_description":    description,
                "ad_category":       category,
                "ad_price":          price,
                "ad_price_original": price_old,
                "seller_name":       seller,
                "location":          location,
                "parsed_at":         datetime.now().isoformat(),
            }

        except Exception as e:
            logger.debug(f"Не удалось распарсить карточку: {e}")
            return None

    async def _extract_prices(self, card) -> tuple[Optional[float], Optional[float]]:
        """Извлечь текущую и старую цену."""
        price = None
        price_old = None
        try:
            # Текущая цена
            price_el = await card.query_selector("[itemprop='price']")
            if price_el:
                content = await price_el.get_attribute("content")
                price = float(content) if content else None

            # Старая цена (перечёркнутая — признак снижения)
            old_el = await card.query_selector("[class*='old-price'], [class*='oldPrice']")
            if old_el:
                old_text = await old_el.inner_text()
                # Убираем всё кроме цифр
                digits = re.sub(r"[^\d]", "", old_text)
                price_old = float(digits) if digits else None

        except Exception:
            pass

        return price, price_old

    async def get_phone(self, listing_url: str) -> Optional[str]:
        """
        Получить телефон с страницы объявления.
        ВНИМАНИЕ: Авито требует авторизации для просмотра телефонов.
        Без авторизации — возвращает None.
        """
        try:
            await self._page.goto(listing_url, wait_until="domcontentloaded", timeout=20_000)
            await self._human_delay()

            # Кнопка "Показать телефон"
            btn = await self._page.query_selector("[data-marker='phone-button']")
            if btn:
                await btn.click()
                await asyncio.sleep(2)
                phone_el = await self._page.query_selector("[class*='phone-number']")
                if phone_el:
                    return (await phone_el.inner_text()).strip()
        except Exception:
            pass
        return None


# ─────────────────────────────────────────────────────────────
#  Оркестратор (запускает весь процесс по всем городам)
# ─────────────────────────────────────────────────────────────

class AvitoParserRunner:
    """
    Запускает парсинг по всем городам и поисковым запросам.
    Передаёт каждый лид через scorer и сохраняет в БД.
    """

    def __init__(self, db=None):
        self.db = db  # Экземпляр Database из database/db.py

    async def run_all_cities(self):
        """Основной метод — запустить полный цикл парсинга."""
        total_new = 0
        total_updated = 0

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )

            for city in config.AVITO_CITIES:
                city = city.strip()
                for query in SEARCH_QUERIES:
                    run_id = None
                    if self.db:
                        run_id = await self.db.start_parse_run(city, query)

                    new_c, upd_c = 0, 0
                    try:
                        new_c, upd_c = await self._run_query(
                            browser, city, query
                        )
                    except Exception as e:
                        logger.error(f"Ошибка в запросе '{query}' / {city}: {e}")
                        if self.db and run_id:
                            await self.db.finish_parse_run(run_id, 0, 0, 0, str(e))
                        continue

                    if self.db and run_id:
                        await self.db.finish_parse_run(
                            run_id,
                            new_c + upd_c,
                            new_c,
                            upd_c,
                        )

                    total_new += new_c
                    total_updated += upd_c

                    # Пауза между запросами
                    await asyncio.sleep(random.uniform(5, 15))

            await browser.close()

        logger.success(
            f"✅ Парсинг завершён: {total_new} новых, {total_updated} обновлённых"
        )
        return total_new, total_updated

    async def _run_query(self, browser, city: str, query: str) -> tuple[int, int]:
        """Парсить один запрос по всем страницам."""
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1280 + random.randint(-100, 100),
                       "height": 800 + random.randint(-50, 50)},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )

        new_count, upd_count = 0, 0

        async with AvitoScraper(context) as scraper:
            for page_num in range(1, config.AVITO_MAX_PAGES + 1):
                listings = await scraper.search_listings(city, query, page_num)

                if not listings:
                    logger.info(f"   Страница {page_num} пуста — стоп")
                    break

                for raw in listings:
                    result = await self._process_listing(raw, city, scraper)
                    if result == "new":
                        new_count += 1
                    elif result == "updated":
                        upd_count += 1

                # Случайная задержка между страницами
                await asyncio.sleep(random.uniform(4, 10))

        await context.close()
        return new_count, upd_count

    async def _process_listing(
        self, raw: dict, city: str, scraper: AvitoScraper
    ) -> str:
        """Скорить лид и сохранить в БД. Вернуть 'new' / 'updated' / 'skip'."""

        # Скоринг
        score_result = score_lead(
            title=raw.get("ad_title", ""),
            description=raw.get("ad_description", ""),
            category_raw=raw.get("ad_category", ""),
            price=raw.get("ad_price"),
            price_original=raw.get("ad_price_original"),
            has_phone=bool(raw.get("phone")),
        )

        # Пропускаем мусор и спам
        if score_result.priority in ("low", "spam"):
            logger.debug(f"   [SKIP] {raw.get('ad_title', '')[:40]} — скор {score_result.total}")
            return "skip"

        # Формируем запись для БД
        lead = {
            **raw,
            "source":          "avito",
            "city":            city,
            "score":           score_result.total,
            "score_breakdown": score_result.to_dict(),
            "priority":        score_result.priority,
        }

        logger.info(
            f"   [+] {score_result.priority.upper():6} | "
            f"скор {score_result.total:3} | "
            f"{raw.get('ad_title', '')[:45]}"
        )

        if self.db:
            lead_id, inserted = await self.db.save_lead(lead)
            if lead_id:
                await self.db.ensure_deep_link(lead_id)
            return "new" if inserted else "updated"

        # Если БД нет — просто печатаем (режим тестирования)
        self._print_lead(lead, score_result)
        return "new"

    @staticmethod
    def _print_lead(lead: dict, score):
        """Красивый вывод в терминал для режима без БД."""
        from rich.console import Console
        from rich.table import Table
        c = Console()
        t = Table(show_header=False, border_style="green" if score.priority == "vip" else "yellow")
        t.add_row("Заголовок", lead.get("ad_title", "")[:60])
        t.add_row("Цена", f"{lead.get('ad_price', 0):,.0f} руб.")
        t.add_row("Балл", f"{score.total} ({score.priority})")
        t.add_row("Сигналы", ", ".join(score.matched_words[:4]))
        t.add_row("URL", lead.get("avito_url", "")[:60])
        c.print(t)


# ─────────────────────────────────────────────────────────────
#  Точка входа для прямого запуска
# ─────────────────────────────────────────────────────────────

async def main():
    """
    Тестовый запуск без БД — просто выводит в терминал.
    Запуск: python -m parser.avito_parser
    """
    logger.info("🚀 Запуск парсера Авито (режим без БД — только вывод в консоль)")
    runner = AvitoParserRunner(db=None)
    await runner.run_all_cities()


if __name__ == "__main__":
    asyncio.run(main())
