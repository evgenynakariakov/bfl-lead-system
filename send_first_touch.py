"""
Generate first-touch messages for new leads and persist outreach result.

Current implementation stores generated text and status in DB.
External delivery channel integration can be added later.
"""
import asyncio
from decimal import Decimal

from loguru import logger

from bot.randomizer import TextRandomizer
from config import config
from database.db import Database


def _price_to_text(value) -> str:
    if value is None:
        return "без цены"
    if isinstance(value, Decimal):
        value = float(value)
    try:
        return f"{float(value):,.0f} руб.".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


async def process_first_touch() -> None:
    db = Database()
    randomizer = TextRandomizer()

    await db.connect()
    try:
        leads = await db.get_lead_for_outreach(
            min_score=config.OUTREACH_MIN_SCORE,
            limit=config.OUTREACH_BATCH_LIMIT,
        )
        if not leads:
            logger.info("Нет лидов для первого касания")
            return

        logger.info(f"Найдено лидов для первого касания: {len(leads)}")

        sent_count = 0
        error_count = 0

        for lead in leads:
            lead_id = lead["id"]
            try:
                deep_link = lead.get("deep_link") or await db.ensure_deep_link(lead_id)
                if not deep_link:
                    raise ValueError("deep_link не сгенерирован. Проверьте BOT_USERNAME в .env")

                message, template_hash = randomizer.first_touch_for_lead(
                    name=lead.get("seller_name") or "",
                    item=lead.get("ad_title") or "объявление",
                    item_price=_price_to_text(lead.get("ad_price")),
                    city=lead.get("city") or "вашем городе",
                    category=lead.get("ad_category") or "",
                    description=lead.get("ad_description") or "",
                    company_name=config.COMPANY_NAME,
                )
                full_message = f"{message}\n\n{deep_link}\n\nref:{template_hash}"

                # Stub delivery step: currently only persistence in DB.
                await db.save_first_touch_result(
                    lead_id=lead_id,
                    first_touch_text=full_message,
                    outreach_status="sent",
                )
                sent_count += 1
                logger.info(f"lead_id={lead_id}: first touch saved")
            except Exception as exc:
                error_count += 1
                await db.save_first_touch_result(
                    lead_id=lead_id,
                    first_touch_text="",
                    outreach_status="error",
                    error_message=f"outreach error: {type(exc).__name__}: {exc}",
                )
                logger.error(f"lead_id={lead_id}: {exc}")

        logger.success(f"Готово: sent={sent_count}, error={error_count}")
    finally:
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(process_first_touch())
