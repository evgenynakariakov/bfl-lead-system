"""
bot/bot_main.py — Telegram-бот для квалификации лидов.

Архитектура: FSM (Finite State Machine)
Каждый пользователь находится в одном из состояний:
    START → ASK_DEBT → ASK_INCOME → ASK_PROPERTY → ASK_CREDITORS → DONE

При квалификации (долг > 300к) → отправляет уведомление менеджеру.

Запуск:
    python -m bot.bot_main

Требуется: BOT_TOKEN и MANAGER_CHAT_ID в .env
"""
import asyncio
import json
import logging
import re
from urllib.parse import unquote

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import InvalidToken, NetworkError, TimedOut
from telegram.request import HTTPXRequest
from loguru import logger

from config import config
from bot.randomizer import TextRandomizer
from database.db import Database


# ─────────────────────────────────────────────────────────────
#  Состояния FSM
# ─────────────────────────────────────────────────────────────

class State:
    START         = "start"
    ASK_DEBT      = "ask_debt"
    ASK_INCOME    = "ask_income"
    ASK_PROPERTY  = "ask_property"
    ASK_CREDITORS = "ask_creditors"
    DONE          = "done"


# ─────────────────────────────────────────────────────────────
#  Парсер сумм долга из свободного текста
# ─────────────────────────────────────────────────────────────

def parse_debt_amount(text: str) -> float:
    """
    \u0412\u044b\u0442\u0430\u0449\u0438\u0442\u044c \u0441\u0443\u043c\u043c\u0443 \u0438\u0437 \u0442\u0435\u043a\u0441\u0442\u0430 \u0432\u0440\u043e\u0434\u0435:
    "500 \u0442\u044b\u0441\u044f\u0447", "1.2 \u043c\u043b\u043d", "300\u043a", "\u043f\u043e\u043b\u0442\u043e\u0440\u0430 \u043c\u0438\u043b\u043b\u0438\u043e\u043d\u0430", "700000"
    \u0412\u043e\u0437\u0432\u0440\u0430\u0449\u0430\u0435\u0442 \u0441\u0443\u043c\u043c\u0443 \u0432 \u0440\u0443\u0431\u043b\u044f\u0445 \u0438\u043b\u0438 0.
    """
    import re

    value = text.lower().strip()

    phrase_map = {
        r"\b\u043f\u043e\u043b\u0442\u043e\u0440\u0430\s+\u043c\u0438\u043b\u043b\u0438\u043e\u043d\u0430\b": 1_500_000.0,
        r"\b\u0434\u0432\u0430\s+\u043c\u0438\u043b\u043b\u0438\u043e\u043d\u0430\b": 2_000_000.0,
        r"\b\u0442\u0440\u0438\s+\u043c\u0438\u043b\u043b\u0438\u043e\u043d\u0430\b": 3_000_000.0,
    }
    for pattern, amount in phrase_map.items():
        if re.search(pattern, value):
            return amount

    million_match = re.search(
        r"(\d+(?:[.,]\d+)?)\s*(?:\u043c\u043b\u043d|\u043c\u0438\u043b\u043b\u0438\u043e\u043d(?:\u0430|\u043e\u0432)?)\b",
        value,
    )
    if million_match:
        return float(million_match.group(1).replace(",", ".")) * 1_000_000

    thousand_match = re.search(
        r"(\d+(?:[.,]\d+)?)\s*(?:\u0442\u044b\u0441|\u0442\u044b\u0441\u044f\u0447|\u043a)\b",
        value,
    )
    if thousand_match:
        return float(thousand_match.group(1).replace(",", ".")) * 1_000

    number_match = re.search(r"\d[\d\s]{2,}\d", value)
    if number_match:
        digits = re.sub(r"\s+", "", number_match.group(0))
        return float(digits)

    short_match = re.search(r"\b\d{1,3}\b", value)
    if short_match:
        return float(short_match.group(0)) * 1_000

    return 0.0

def format_amount(amount: float) -> str:
    """700000 -> '700 000 \u0440\u0443\u0431.'"""
    if amount >= 1_000_000:
        return f"{amount/1_000_000:.1f} \u043c\u043b\u043d \u0440\u0443\u0431."
    return f"{amount:,.0f} \u0440\u0443\u0431.".replace(",", " ")


# ─────────────────────────────────────────────────────────────
#  Клавиатуры
# ─────────────────────────────────────────────────────────────

KB_YES_NO = ReplyKeyboardMarkup(
    [["✅ Да", "❌ Нет"]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

KB_DEBT_RANGES = ReplyKeyboardMarkup(
    [
        ["До 300 000 руб.", "300 000 – 500 000 руб."],
        ["500 000 – 1 млн руб.", "Больше 1 млн руб."],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)

KB_CREDITORS = ReplyKeyboardMarkup(
    [
        ["1-2 кредитора", "3-5 кредиторов"],
        ["6-10 кредиторов", "Больше 10"],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)

REMOVE_KB = ReplyKeyboardRemove()


# ─────────────────────────────────────────────────────────────
#  Хранилище сессий (в памяти; для продакшена — заменить на DB)
# ─────────────────────────────────────────────────────────────

class SessionStore:
    """
    In-memory хранилище сессий.
    В продакшене замените на вызовы db.upsert_session() / db.get_session().
    """
    def __init__(self):
        self._sessions: dict[int, dict] = {}

    def get(self, chat_id: int) -> dict:
        return self._sessions.get(chat_id, {"step": State.START, "data": {}})

    def set_step(self, chat_id: int, step: str):
        if chat_id not in self._sessions:
            self._sessions[chat_id] = {"step": State.START, "data": {}}
        self._sessions[chat_id]["step"] = step

    def save_data(self, chat_id: int, key: str, value):
        if chat_id not in self._sessions:
            self._sessions[chat_id] = {"step": State.START, "data": {}}
        self._sessions[chat_id]["data"][key] = value

    def get_data(self, chat_id: int) -> dict:
        return self._sessions.get(chat_id, {}).get("data", {})

    def clear(self, chat_id: int):
        self._sessions.pop(chat_id, None)


sessions = SessionStore()
randomizer = TextRandomizer()


async def _load_lead_by_id(lead_id: int) -> dict | None:
    db = Database()
    await db.connect()
    try:
        return await db.get_lead(lead_id)
    finally:
        await db.disconnect()


async def _bind_lead_to_chat(lead_id: int, chat_id: int, username: str) -> None:
    db = Database()
    await db.connect()
    try:
        await db.link_telegram(lead_id, chat_id, username)
        await db.upsert_session(chat_id, lead_id, State.START, {})
    finally:
        await db.disconnect()


async def _save_qualification_result(chat_id: int, data: dict) -> None:
    lead_id = data.get("lead_id")
    if not lead_id:
        return

    db = Database()
    await db.connect()
    try:
        await db.set_lead_qualification(lead_id, data)
        await db.upsert_session(chat_id, lead_id, State.DONE, data)
    finally:
        await db.disconnect()


# ─────────────────────────────────────────────────────────────
#  Хэндлеры бота
# ─────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start — запускается когда пользователь кликает по ссылке
    или пишет боту первый раз.

    Ожидаем строгий deep link: /start lead_<lead_id>_<item>_<city>
    """
    chat_id = update.effective_chat.id
    user = update.effective_user

    lead_info = {}
    if context.args:
        payload = unquote(context.args[0].strip())
        match = re.match(r"^lead_(\d+)(?:_(.*))?$", payload)
        if match:
            lead_id = int(match.group(1))
            lead_info["lead_id"] = lead_id
            extra = (match.group(2) or "").strip()
            if extra:
                parts = extra.split("_")
                if len(parts) >= 2:
                    lead_info["item"] = "_".join(parts[:-1]).replace("-", " ")
                    lead_info["city"] = parts[-1].replace("-", " ")
                else:
                    lead_info["item"] = extra.replace("-", " ")

            await _bind_lead_to_chat(lead_id, chat_id, user.username or "")
            lead_row = await _load_lead_by_id(lead_id)
            if lead_row:
                lead_info["item"] = lead_row.get("ad_title") or lead_info.get("item", "")
                lead_info["city"] = lead_row.get("city") or lead_info.get("city", "")
            logger.info(f"Deep-link accepted: lead_id={lead_id}, payload='{payload}'")
        else:
            logger.warning(f"Некорректный deep-link payload: {payload}")

    name = user.first_name or ""
    sessions.save_data(chat_id, "name", name)
    sessions.save_data(chat_id, "username", user.username or "")
    for key, value in lead_info.items():
        sessions.save_data(chat_id, key, value)
    sessions.set_step(chat_id, State.START)

    item = lead_info.get("item", "").replace("-", " ") or "объявление"
    city = lead_info.get("city", "").replace("-", " ") or "вашем городе"

    welcome = (
        f"👋 Привет, {name}!\n\n"
        f"Благодарю, что откликнулись.\n\n"
        f"Я — бот юридической службы по банкротству физических лиц. "
        f"Помогаю людям официально списать долги по ФЗ-127.\n\n"
        f"{'Вижу, вы продаёте «' + item + '» — ' if item != 'объявление' else ''}"
        f"Хочу задать пару вопросов чтобы понять, "
        f"можем ли мы вам помочь. Займёт 2-3 минуты. Готовы?"
    )

    await update.message.reply_text(
        welcome,
        reply_markup=KB_YES_NO,
    )
    sessions.set_step(chat_id, State.START)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главный роутер — разводит по шагам FSM."""
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    session = sessions.get(chat_id)
    step = session["step"]

    logger.debug(f"chat={chat_id} | step={step} | text={text[:30]}")

    if step == State.START:
        await _handle_start(update, text, chat_id)

    elif step == State.ASK_DEBT:
        await _handle_debt(update, text, chat_id)

    elif step == State.ASK_INCOME:
        await _handle_income(update, text, chat_id)

    elif step == State.ASK_PROPERTY:
        await _handle_property(update, text, chat_id)

    elif step == State.ASK_CREDITORS:
        await _handle_creditors(update, text, chat_id, context)

    else:
        await update.message.reply_text(
            "Если хотите начать заново — напишите /start",
            reply_markup=REMOVE_KB,
        )


async def _handle_start(update: Update, text: str, chat_id: int):
    """Пользователь ответил на приветствие."""
    if _is_yes(text):
        msg = randomizer.bot_response("ask_debt")
        await update.message.reply_text(
            f"Отлично! 👍\n\n{msg}\n\n"
            f"Можно выбрать диапазон или написать своими словами.",
            reply_markup=KB_DEBT_RANGES,
        )
        sessions.set_step(chat_id, State.ASK_DEBT)
    else:
        await update.message.reply_text(
            "Понял, не настаиваю. Если когда-нибудь понадоблюсь — я здесь 👋",
            reply_markup=REMOVE_KB,
        )
        sessions.set_step(chat_id, State.DONE)


async def _handle_debt(update: Update, text: str, chat_id: int):
    """Пользователь назвал сумму долга."""
    amount = parse_debt_amount(text)

    # Ловим выбор кнопки
    if "до 300" in text.lower():
        amount = 200_000
    elif "300" in text and "500" in text:
        amount = 400_000
    elif "500" in text and "млн" in text.lower():
        amount = 750_000
    elif "1 млн" in text.lower() or "больше 1" in text.lower():
        amount = 1_500_000

    sessions.save_data(chat_id, "debt_amount", amount)
    logger.info(f"chat={chat_id} | Сумма долга: {amount:,.0f} руб.")

    if amount < 300_000 and amount > 0:
        # Не наш клиент — мягкий отказ
        msg = randomizer.bot_response("debt_too_low")
        await update.message.reply_text(msg, reply_markup=REMOVE_KB)
        sessions.set_step(chat_id, State.DONE)
        sessions.save_data(chat_id, "qualified", False)
        sessions.save_data(chat_id, "reject_reason", "debt_too_low")
    elif amount == 0:
        # Не смогли распарсить — переспрашиваем
        await update.message.reply_text(
            "Не смог разобрать сумму 🤔 Напишите, пожалуйста, цифрами:\n"
            "Например: «500 тысяч» или «1.2 млн» или просто «700000»"
        )
    else:
        # Подходит! Двигаемся дальше
        msg = randomizer.bot_response("ask_income")
        await update.message.reply_text(
            f"Понял, {format_amount(amount)} — это серьёзно. "
            f"Мы работаем именно с такими ситуациями ✅\n\n{msg}",
            reply_markup=KB_YES_NO,
        )
        sessions.set_step(chat_id, State.ASK_INCOME)


async def _handle_income(update: Update, text: str, chat_id: int):
    """Пользователь ответил об официальном доходе."""
    has_income = _is_yes(text)
    sessions.save_data(chat_id, "has_income", has_income)

    msg = randomizer.bot_response("ask_property")
    await update.message.reply_text(
        f"{'Отлично — это важно для процедуры.' if has_income else 'Понял.'}\n\n{msg}",
        reply_markup=KB_YES_NO,
    )
    sessions.set_step(chat_id, State.ASK_PROPERTY)


async def _handle_property(update: Update, text: str, chat_id: int):
    """Пользователь ответил о наличии имущества."""
    has_property = _is_yes(text)
    sessions.save_data(chat_id, "has_property", has_property)

    msg = randomizer.bot_response("ask_creditors")
    await update.message.reply_text(msg, reply_markup=KB_CREDITORS)
    sessions.set_step(chat_id, State.ASK_CREDITORS)


async def _handle_creditors(
    update: Update, text: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE
):
    """Последний вопрос — количество кредиторов. Затем — результат."""
    # Парсим количество
    import re
    nums = re.findall(r"\d+", text)
    creditors = int(nums[0]) if nums else 1

    sessions.save_data(chat_id, "creditors_count", creditors)
    sessions.set_step(chat_id, State.DONE)
    sessions.save_data(chat_id, "qualified", True)

    data = sessions.get_data(chat_id)
    await _save_qualification_result(chat_id, data)

    # Финальное сообщение
    msg = randomizer.bot_response("qualified_handover")
    await update.message.reply_text(msg, reply_markup=REMOVE_KB)

    # Уведомление менеджеру
    await _notify_manager(context, chat_id, data)


async def _notify_manager(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, data: dict
):
    """Отправить карточку квалифицированного лида менеджеру."""
    if not config.MANAGER_CHAT_ID:
        logger.warning("MANAGER_CHAT_ID не задан — уведомление не отправлено")
        return

    name     = data.get("name", "—")
    username = data.get("username", "—")
    debt     = data.get("debt_amount", 0)
    income   = "✅ Есть" if data.get("has_income") else "❌ Нет"
    prop     = "✅ Есть" if data.get("has_property") else "❌ Нет"
    cred     = data.get("creditors_count", "—")
    item     = data.get("item", "—")
    city     = data.get("city", "—")

    card = (
        f"🔥 *НОВЫЙ КВАЛИФИЦИРОВАННЫЙ ЛИД*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Имя: *{name}*\n"
        f"📱 Username: @{username}\n"
        f"💬 Chat ID: `{chat_id}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Сумма долга: *{format_amount(debt)}*\n"
        f"💼 Официальный доход: {income}\n"
        f"🏠 Имущество: {prop}\n"
        f"🏦 Кредиторов: *{cred}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Продавал: {item}\n"
        f"📍 Город: {city}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"▶️ Написать: tg://user?id={chat_id}"
    )

    await context.bot.send_message(
        chat_id=config.MANAGER_CHAT_ID,
        text=card,
        parse_mode="Markdown",
    )
    logger.success(f"✅ Уведомление менеджеру отправлено | chat_id={chat_id}")


# ─────────────────────────────────────────────────────────────
#  Вспомогательные функции
# ─────────────────────────────────────────────────────────────

def _is_yes(text: str) -> bool:
    """Считать ли ответ утвердительным."""
    text = text.lower().strip()
    YES_WORDS = ["да", "yes", "конечно", "готов", "готова", "ок", "ok",
                 "✅", "хорошо", "давай", "давайте", "согласен", "согласна",
                 "есть", "имеется", "работаю", "работаете", "пенсия"]
    return any(w in text for w in YES_WORDS)


# ─────────────────────────────────────────────────────────────
#  Запуск
# ─────────────────────────────────────────────────────────────

async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler to keep logs readable and avoid silent crashes."""
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        logger.warning(f"Telegram network issue: {type(err).__name__}: {err}")
        return
    logger.exception(f"Unhandled bot error: {err}")


def run_bot():
    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN не задан в .env!")
        return

    logger.info("🤖 Запуск Telegram-бота...")

    request = HTTPXRequest(
        connection_pool_size=32,
        read_timeout=30.0,
        write_timeout=30.0,
        connect_timeout=30.0,
        pool_timeout=30.0,
        http_version="1.1",
        httpx_kwargs={"trust_env": False},
    )
    get_updates_request = HTTPXRequest(
        connection_pool_size=16,
        read_timeout=60.0,
        write_timeout=30.0,
        connect_timeout=30.0,
        pool_timeout=30.0,
        http_version="1.1",
        httpx_kwargs={"trust_env": False},
    )
    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .concurrent_updates(False)
        .request(request)
        .get_updates_request(get_updates_request)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    app.add_error_handler(_on_error)

    logger.success("✅ Бот запущен. Ctrl+C для остановки.")
    try:
        app.run_polling(
            drop_pending_updates=True,
            poll_interval=0.5,
            timeout=30,
            read_timeout=30,
            write_timeout=30,
            connect_timeout=30,
            pool_timeout=30,
            bootstrap_retries=-1,
        )
    except InvalidToken:
        logger.error("BOT_TOKEN невалиден. Проверьте .env и токен от BotFather.")
    except Exception as exc:
        logger.exception(f"Не удалось запустить бота: {exc}")


if __name__ == "__main__":
    run_bot()
