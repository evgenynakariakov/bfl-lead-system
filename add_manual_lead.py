"""
Add one manual lead to DB for demo flows.

Usage:
    python add_manual_lead.py
    python add_manual_lead.py --title "Срочно продам авто" --city "kazan"
"""
import argparse
import asyncio
import time

from loguru import logger

from database.db import Database
from lead_parser.scorer import score_lead


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value if value else default


def _to_float(text: str | None) -> float | None:
    if text is None:
        return None
    t = text.strip().replace(" ", "").replace(",", ".")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add manual lead to PostgreSQL")
    parser.add_argument("--no-input", action="store_true", help="Do not ask interactive questions, use defaults")
    parser.add_argument("--title", type=str, default="")
    parser.add_argument("--description", type=str, default="")
    parser.add_argument("--city", type=str, default="kazan")
    parser.add_argument("--category", type=str, default="авто")
    parser.add_argument("--price", type=str, default="")
    parser.add_argument("--price-original", type=str, default="")
    parser.add_argument("--seller-name", type=str, default="Eugene")
    parser.add_argument("--phone", type=str, default="")
    parser.add_argument("--url", type=str, default="")
    return parser.parse_args()


async def add_manual_lead(args: argparse.Namespace) -> None:
    def resolve(value: str, prompt: str, default: str = "") -> str:
        if value:
            return value
        if args.no_input:
            return default
        return _ask(prompt, default)

    title = resolve(args.title, "Заголовок объявления", "Срочно продам авто, нужны деньги")
    description = resolve(
        args.description,
        "Описание",
        "Продаю вынужденно, срочно нужны деньги, торг уместен.",
    )
    city = resolve(args.city, "Город (slug)", "kazan")
    category = resolve(args.category, "Категория", "авто")
    seller_name = resolve(args.seller_name, "Имя продавца", "Eugene")
    phone = resolve(args.phone, "Телефон (опционально)", "")
    url = resolve(args.url, "Ссылка Avito (опционально)", "")
    price = _to_float(resolve(args.price, "Цена (опционально)", "700000"))
    price_original = _to_float(resolve(args.price_original, "Старая цена (опционально)", "900000"))

    score = score_lead(
        title=title,
        description=description,
        category_raw=category,
        price=price,
        price_original=price_original,
        has_phone=bool(phone),
    )

    avito_id = f"manual_{int(time.time() * 1000)}"
    lead = {
        "source": "avito",
        "avito_id": avito_id,
        "avito_url": url,
        "city": city,
        "ad_title": title,
        "ad_description": description,
        "ad_category": category,
        "ad_price": price,
        "ad_price_original": price_original,
        "seller_name": seller_name,
        "phone": phone or None,
        "score": score.total,
        "score_breakdown": score.to_dict(),
        "priority": score.priority,
    }

    db = Database()
    await db.connect()
    try:
        lead_id, inserted = await db.save_lead(lead)
        if not lead_id:
            raise RuntimeError("Не удалось сохранить лид")
        deep_link = await db.ensure_deep_link(lead_id)
        logger.success(f"lead_id={lead_id} saved (inserted={inserted})")
        logger.info(f"score={score.total}, priority={score.priority}")
        logger.info(f"deep_link={deep_link or 'EMPTY (проверь BOT_USERNAME в .env)'}")
    finally:
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(add_manual_lead(parse_args()))
