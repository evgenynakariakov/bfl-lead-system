"""
Seed demo/test leads into PostgreSQL for local showcase.

Usage:
    python seed_test_data.py
    python seed_test_data.py --count 80 --wipe
"""
import argparse
import asyncio
import random
from datetime import datetime, timedelta

from loguru import logger

from database.db import Database


CITIES = [
    "moskva",
    "sankt-peterburg",
    "kazan",
    "novosibirsk",
    "ekaterinburg",
    "krasnodar",
]

TITLES = [
    "Срочно продам Honda Civic 2019",
    "Продам iPhone 15 Pro, торг",
    "Срочно, продаю мебель из квартиры",
    "Продам Toyota Camry, нужны деньги",
    "Продам ноутбук MacBook Pro",
    "Срочная продажа квартиры, торг уместен",
    "Продам технику, переезд",
    "Продам авто, цена снижена",
]

DESCRIPTIONS = [
    "Срочная продажа, нужны деньги, возможен торг.",
    "Хорошее состояние, цена ниже рынка, быстрый выход на сделку.",
    "Продаю вынужденно по финансовым причинам.",
    "Срочно, без обмена, только продажа.",
    "Торг уместен, документы в порядке.",
]

PRIORITIES = ["vip", "high", "medium", "low"]
STATUSES = ["new", "contacted", "responded", "in_bot", "qualified", "rejected"]


def _priority_from_score(score: int) -> str:
    if score >= 80:
        return "vip"
    if score >= 60:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


async def seed(count: int, wipe: bool) -> None:
    db = Database()
    await db.connect()
    try:
        async with db.pool.acquire() as conn:
            if wipe:
                await conn.execute("TRUNCATE TABLE messages, bot_sessions, leads RESTART IDENTITY CASCADE")
                logger.info("Таблицы leads/messages/bot_sessions очищены")

            base_time = datetime.now() - timedelta(days=14)

            for i in range(count):
                score = random.randint(20, 95)
                status = random.choices(
                    population=STATUSES,
                    weights=[35, 20, 15, 10, 15, 5],
                    k=1,
                )[0]
                outreach_status = "new"
                first_touch_text = None
                first_touch_at = None
                contact_attempts = 0
                last_contact_at = None

                # Ensure enough records for send_first_touch.py filters.
                if i < max(10, count // 4):
                    status = "new"
                    score = random.randint(45, 90)
                    outreach_status = "new"
                elif status in {"contacted", "responded", "in_bot", "qualified"}:
                    outreach_status = random.choice(["sent", "error"])
                    contact_attempts = random.randint(1, 3)
                    first_touch_at = base_time + timedelta(days=random.randint(0, 12), hours=random.randint(0, 23))
                    last_contact_at = first_touch_at + timedelta(minutes=random.randint(5, 600))
                    if outreach_status == "sent":
                        first_touch_text = "Тестовое первое касание (seed)."

                title = random.choice(TITLES)
                city = random.choice(CITIES)
                price = random.randint(120_000, 2_700_000)
                created_at = base_time + timedelta(days=random.randint(0, 14), hours=random.randint(0, 23))

                await conn.execute(
                    """
                    INSERT INTO leads (
                        source, avito_id, avito_url, city,
                        ad_title, ad_description, ad_category,
                        ad_price, ad_price_original, seller_name, phone,
                        score, score_breakdown, priority, status,
                        deep_link, outreach_status, first_touch_text, first_touch_at,
                        contact_attempts, last_contact_at, created_at, updated_at
                    ) VALUES (
                        'avito', $1, $2, $3,
                        $4, $5, $6,
                        $7, $8, $9, $10,
                        $11, $12::jsonb, $13, $14,
                        $15, $16, $17, $18,
                        $19, $20, $21, NOW()
                    )
                    ON CONFLICT (avito_id) DO NOTHING
                    """,
                    f"seed_{int(created_at.timestamp())}_{i}",
                    f"https://www.avito.ru/{city}/item_seed_{i}",
                    city,
                    title,
                    random.choice(DESCRIPTIONS),
                    random.choice(["авто", "электроника", "недвижимость"]),
                    price,
                    int(price * random.uniform(1.05, 1.25)),
                    f"Продавец_{i}",
                    f"+7999{random.randint(1000000, 9999999)}",
                    score,
                    '{"seed": true}',
                    _priority_from_score(score),
                    status,
                    None,
                    outreach_status,
                    first_touch_text,
                    first_touch_at,
                    contact_attempts,
                    last_contact_at,
                    created_at,
                )

            logger.success(f"Добавлены тестовые лиды: {count}")

            rows = await conn.fetch(
                """
                SELECT status, outreach_status, COUNT(*) AS cnt
                FROM leads
                GROUP BY status, outreach_status
                ORDER BY cnt DESC
                """
            )
            for r in rows:
                logger.info(f"status={r['status']}, outreach={r['outreach_status']}: {r['cnt']}")

    finally:
        await db.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed demo leads into database")
    parser.add_argument("--count", type=int, default=60, help="How many leads to generate")
    parser.add_argument("--wipe", action="store_true", help="Truncate leads/messages/bot_sessions before seed")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(seed(args.count, args.wipe))
