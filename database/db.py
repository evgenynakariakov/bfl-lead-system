"""
database/db.py - async PostgreSQL data-access layer (asyncpg).

Usage:
    from database.db import Database
    db = Database()
    await db.connect()
    lead_id = await db.save_lead(lead_data)
    await db.disconnect()
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Optional

import asyncpg
from loguru import logger

from config import config


class Database:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        """Create asyncpg connection pool."""
        self.pool = await asyncpg.create_pool(
            dsn=config.db_dsn,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        logger.info("DB connection established")

    async def disconnect(self):
        if self.pool:
            await self.pool.close()
            logger.info("DB connection closed")

    # Leads

    async def save_lead(self, lead: dict) -> tuple[Optional[int], bool]:
        """
        Insert or update lead by avito_id.
        Returns tuple: (lead_id, inserted_flag).
        """
        sql = """
            INSERT INTO leads (
                source, avito_id, avito_url, city,
                ad_title, ad_description, ad_category,
                ad_price, ad_price_original, seller_name, phone,
                score, score_breakdown, priority
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6, $7,
                $8, $9, $10, $11,
                $12, $13, $14
            )
            ON CONFLICT (avito_id)
            DO UPDATE SET
                score           = EXCLUDED.score,
                score_breakdown = EXCLUDED.score_breakdown,
                priority        = EXCLUDED.priority,
                ad_price        = EXCLUDED.ad_price,
                updated_at      = NOW()
            RETURNING id, (xmax = 0) AS inserted
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                sql,
                lead.get("source", "avito"),
                lead.get("avito_id"),
                lead.get("avito_url"),
                lead.get("city"),
                lead.get("ad_title"),
                lead.get("ad_description"),
                lead.get("ad_category"),
                lead.get("ad_price"),
                lead.get("ad_price_original"),
                lead.get("seller_name"),
                lead.get("phone"),
                lead.get("score", 0),
                json.dumps(lead.get("score_breakdown", {})),
                lead.get("priority", "low"),
            )
            if not row:
                return None, False
            return row["id"], bool(row["inserted"])

    @staticmethod
    def _slug(value: str, default: str = "unknown") -> str:
        raw = (value or "").strip().lower()
        ascii_raw = (
            unicodedata.normalize("NFKD", raw)
            .encode("ascii", "ignore")
            .decode("ascii")
        )
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", ascii_raw).strip("-")
        return slug or default

    def build_lead_deep_link(self, lead_id: int, item: str, city: str) -> str:
        if not config.BOT_USERNAME:
            return ""

        base_payload = f"lead_{lead_id}"
        item_slug = self._slug(item, default="item")
        city_slug = self._slug(city, default="city")
        full_payload = f"{base_payload}_{item_slug}_{city_slug}"

        payload = (
            full_payload
            if len(full_payload) <= 64 and re.fullmatch(r"[A-Za-z0-9_-]+", full_payload)
            else base_payload
        )
        return f"https://t.me/{config.BOT_USERNAME}?start={payload}"

    async def ensure_deep_link(self, lead_id: int) -> Optional[str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, ad_title, city, deep_link FROM leads WHERE id = $1",
                lead_id,
            )
            if not row:
                return None

            existing = row["deep_link"] or ""
            if existing:
                valid = False
                if config.BOT_USERNAME:
                    match = re.match(
                        rf"^https://t\.me/{re.escape(config.BOT_USERNAME)}\?start=([A-Za-z0-9_-]+)$",
                        existing,
                    )
                    if match and len(match.group(1)) <= 64:
                        valid = True
                if valid:
                    return existing

            deep_link = self.build_lead_deep_link(
                lead_id=lead_id,
                item=row["ad_title"] or "item",
                city=row["city"] or "city",
            )
            if deep_link:
                await conn.execute(
                    "UPDATE leads SET deep_link = $2, updated_at = NOW() WHERE id = $1",
                    lead_id,
                    deep_link,
                )
            return deep_link

    async def get_lead(self, lead_id: int) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM leads WHERE id = $1", lead_id)
            return dict(row) if row else None

    async def get_lead_for_outreach(
        self,
        min_score: int,
        limit: int = 50,
    ) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM leads
                WHERE status = 'new'
                  AND COALESCE(outreach_status, 'new') = 'new'
                  AND score >= $1
                ORDER BY score DESC, created_at DESC
                LIMIT $2
                """,
                min_score,
                limit,
            )
            return [dict(r) for r in rows]

    async def get_lead_by_chat(self, chat_id: int) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM leads WHERE telegram_chat_id = $1",
                chat_id,
            )
            return dict(row) if row else None

    async def update_lead_status(self, lead_id: int, status: str, **extra):
        """Update lead status and optional extra fields."""
        fields = {"status": status, **extra}
        set_parts = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(fields))
        values = list(fields.values())
        sql = f"UPDATE leads SET {set_parts} WHERE id = $1"
        async with self.pool.acquire() as conn:
            await conn.execute(sql, lead_id, *values)

    async def link_telegram(self, lead_id: int, chat_id: int, username: str = None):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE leads SET
                    telegram_chat_id = $2,
                    telegram_username = $3,
                    status = CASE
                        WHEN status IN ('new', 'contacted', 'responded') THEN 'in_bot'
                        ELSE status
                    END,
                    updated_at = NOW()
                WHERE id = $1
                """,
                lead_id,
                chat_id,
                username,
            )

    async def save_first_touch_result(
        self,
        lead_id: int,
        first_touch_text: str,
        outreach_status: str,
        error_message: str | None = None,
    ):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE leads SET
                    first_touch_text = $2,
                    first_touch_at = NOW(),
                    outreach_status = $3::varchar,
                    status = CASE
                        WHEN $3::varchar = 'sent' AND status = 'new' THEN 'contacted'
                        WHEN $3::varchar = 'error' THEN status
                        ELSE status
                    END,
                    notes = CASE
                        WHEN $4::TEXT IS NULL THEN notes
                        WHEN notes IS NULL OR notes = '' THEN $4
                        ELSE notes || E'\n' || $4
                    END,
                    contact_attempts = COALESCE(contact_attempts, 0) + 1,
                    last_contact_at = NOW(),
                    updated_at = NOW()
                WHERE id = $1
                """,
                lead_id,
                first_touch_text,
                outreach_status,
                error_message,
            )

    async def get_new_leads_for_contact(self, limit: int = 50) -> list[dict]:
        """Return leads ready for first touch, ordered by score."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM leads
                WHERE status = 'new'
                  AND score >= 30
                  AND phone IS NOT NULL
                ORDER BY score DESC, created_at DESC
                LIMIT $1
                """,
                limit,
            )
            return [dict(r) for r in rows]

    async def set_lead_qualification(self, lead_id: int, data: dict):
        """Save qualification answers from bot dialog."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE leads SET
                    debt_amount         = $2,
                    has_income          = $3,
                    has_property        = $4,
                    creditors_count     = $5,
                    qualification_data  = $6,
                    status              = 'qualified',
                    updated_at          = NOW()
                WHERE id = $1
                """,
                lead_id,
                data.get("debt_amount"),
                data.get("has_income"),
                data.get("has_property"),
                data.get("creditors_count"),
                json.dumps(data),
            )

    # Bot sessions

    async def get_session(self, chat_id: int) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM bot_sessions WHERE telegram_chat_id = $1",
                chat_id,
            )
            return dict(row) if row else None

    async def upsert_session(
        self,
        chat_id: int,
        lead_id: int,
        step: str,
        data: dict,
    ):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO bot_sessions (telegram_chat_id, lead_id, current_step, session_data)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (telegram_chat_id)
                DO UPDATE SET
                    current_step = EXCLUDED.current_step,
                    session_data = EXCLUDED.session_data,
                    updated_at   = NOW()
                """,
                chat_id,
                lead_id,
                step,
                json.dumps(data),
            )

    async def delete_session(self, chat_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM bot_sessions WHERE telegram_chat_id = $1",
                chat_id,
            )

    # Messages

    async def log_message(
        self,
        lead_id: int,
        direction: str,
        text: str,
        channel: str = "telegram",
    ):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO messages (lead_id, channel, direction, text)
                VALUES ($1, $2, $3, $4)
                """,
                lead_id,
                channel,
                direction,
                text,
            )

    # Parse runs

    async def start_parse_run(self, city: str, query: str) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO parse_runs (city, search_query, status)
                VALUES ($1, $2, 'running')
                RETURNING id
                """,
                city,
                query,
            )
            return row["id"]

    async def finish_parse_run(
        self,
        run_id: int,
        total: int,
        new: int,
        updated: int,
        error: str = None,
    ):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE parse_runs SET
                    finished_at   = NOW(),
                    total_found   = $2,
                    new_leads     = $3,
                    updated_leads = $4,
                    status        = CASE WHEN $5::TEXT IS NULL THEN 'done' ELSE 'error' END,
                    error_message = $5
                WHERE id = $1
                """,
                run_id,
                total,
                new,
                updated,
                error,
            )

    # Dashboard data

    async def get_funnel(self) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM funnel_stats")
            return [dict(r) for r in rows]

    async def get_daily_stats(self, days: int = 14) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM daily_stats WHERE day >= NOW() - INTERVAL '1 day' * $1",
                days,
            )
            return [dict(r) for r in rows]

    async def get_top_leads(self, limit: int = 20) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, city, ad_title, ad_category, ad_price,
                       score, priority, status, created_at, phone
                FROM leads
                ORDER BY score DESC, created_at DESC
                LIMIT $1
                """,
                limit,
            )
            return [dict(r) for r in rows]
