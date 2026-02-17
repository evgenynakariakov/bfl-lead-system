"""
First-touch text generator and bot phrase randomizer.

Design goals:
- human-like wording (not robotic template spam),
- transparent company identity,
- context-based message angle from ad title/description/category,
- deterministic variation support via optional seed.
"""
from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass
from typing import Optional


BOT_RESPONSE_TEMPLATES = {
    "welcome": [
        "Отлично, спасибо за ответ. Задам пару коротких вопросов, это займёт 2 минуты.",
        "Хорошо, пройдёмся по 3-4 пунктам и поймём, можем ли мы помочь.",
    ],
    "ask_debt": [
        "Подскажите, какая у вас примерно общая сумма долгов (банки, МФО, физлица)?",
        "Первый вопрос: ориентировочно сколько составляет общий долг?",
        "Сколько сейчас примерно по всем обязательствам вместе?",
    ],
    "debt_too_low": [
        "Понял. Мы обычно работаем с долгом от 300 000 руб. Если ситуация изменится, напишите — подскажем.",
        "Для суммы до 300k процедура обычно невыгодна. Если долг вырастет — можно вернуться к вопросу.",
    ],
    "ask_income": [
        "Есть ли сейчас официальный доход (зарплата, пенсия, ИП)?",
        "Подскажите, есть официальный источник дохода?",
    ],
    "ask_property": [
        "Есть ли в собственности имущество (кроме единственного жилья)?",
        "Имущество на вас оформлено: авто, доля, недвижимость?",
    ],
    "ask_creditors": [
        "Сколько примерно кредиторов: банки, МФО, физлица?",
        "Ориентировочно сколько у вас кредиторов?",
    ],
    "qualified_handover": [
        "Вы подходите под предварительные критерии. Передаю вас юристу, он свяжется в ближайшее время.",
        "Спасибо за ответы. Передаю карточку юристу, он напишет/позвонит в ближайшее время.",
    ],
    "not_qualified": [
        "Спасибо за ответы. По текущим параметрам мы не сможем помочь, но если ситуация изменится — напишите.",
    ],
}


@dataclass
class LeadContext:
    asset: str
    pressure: str
    support_angle: str
    city_phrase: str


FIRST_TOUCH_STRUCTURES = [
    (
        "{hello} {name_part}Мы компания БФЛ, увидели ваше объявление «{item}»{price_part}. "
        "{problem_line} {support_line} Если актуально, могу прислать короткий разбор в 3 пунктах."
    ),
    (
        "{hello} Пишу по объявлению «{item}»{price_part}. Мы в БФЛ помогаем людям законно решать долговую нагрузку. "
        "{problem_line} {support_line} Если хотите, дам быстрый чек-лист и ссылку на бота."
    ),
    (
        "{hello} По вашему объявлению «{item}»{price_part} видно {pressure}. "
        "Мы компания БФЛ, работаем с такими кейсами регулярно. {support_line} "
        "Если тема актуальна — могу отправить краткий план действий."
    ),
]


HELLOS = ["Здравствуйте.", "Добрый день.", "Приветствую."]


SYNONYMS = {
    "видно": ["видно", "похоже", "часто в таких случаях"],
    "оперативно": ["оперативно", "без затяжки", "в короткие сроки"],
    "законно": ["законно", "официально", "в рамках закона"],
    "помочь": ["помочь", "подсказать", "дать понятный алгоритм"],
}


PRESSURE_PATTERNS = [
    (r"(срочн|срочно|быстро|вынужден|нужны деньги|деньги нужны)", "что продажа идёт в срочном режиме"),
    (r"(долг|кредит|мфо|пристав|суд|коллектор)", "признаки финансовой нагрузки"),
    (r"(торг|цена снижена|скидка)", "что продажа идёт с дисконтом"),
]


ASSET_PATTERNS = [
    (r"(авто|автомоб|машин|toyota|honda|kia|hyundai|bmw|mercedes)", "автомобиль"),
    (r"(квартир|дом|недвижим|ипотек|участок|студия)", "недвижимость"),
    (r"(iphone|телефон|смартфон|ноутбук|macbook|техник)", "техника"),
    (r"(мебел|диван|кресл|шкаф|стол)", "мебель"),
]


class TextRandomizer:
    def __init__(self, seed: Optional[str] = None):
        self._seed = seed

    def _rng(self) -> random.Random:
        return random.Random(self._seed) if self._seed else random.Random()

    def _detect_asset(self, title: str, category: str) -> str:
        src = f"{title} {category}".lower()
        for pattern, label in ASSET_PATTERNS:
            if re.search(pattern, src):
                return label
        return "имущество"

    def _detect_pressure(self, title: str, description: str) -> str:
        src = f"{title} {description}".lower()
        for pattern, label in PRESSURE_PATTERNS:
            if re.search(pattern, src):
                return label
        return "финансовая неопределённость"

    def _support_line(self, asset: str, pressure: str, city: str, rng: random.Random) -> str:
        line_bank = [
            f"Если причина связана с долгами, можем {rng.choice(SYNONYMS['оперативно'])} показать варианты {rng.choice(SYNONYMS['законно'])} решения.",
            f"По таким ситуациям обычно можно {rng.choice(SYNONYMS['законно'])} снизить давление и выстроить план без хаотичных продаж.",
            f"В {city} мы уже вели похожие случаи: сначала диагностика, затем понятные шаги без лишних обещаний.",
        ]
        if asset == "автомобиль":
            line_bank.append(
                "По авто-кейсам часто удаётся разобрать ситуацию так, чтобы не принимать решения в спешке."
            )
        if "дисконтом" in pressure:
            line_bank.append(
                "Если цена уже снижалась, это хороший момент сначала проверить правовой сценарий, а потом принимать финальное решение."
            )
        return rng.choice(line_bank)

    def _problem_line(self, pressure: str) -> str:
        return f"Понимаю, что {pressure}." if pressure else "Понимаю, что ситуация может быть непростой."

    def first_touch_for_lead(
        self,
        *,
        name: str = "",
        item: str = "объявление",
        item_price: str = "",
        city: str = "",
        category: str = "",
        description: str = "",
        company_name: str = "БФЛ",
    ) -> tuple[str, str]:
        rng = self._rng()
        hello = rng.choice(HELLOS)

        asset = self._detect_asset(item, category)
        pressure = self._detect_pressure(item, description)
        city_phrase = city or "вашем городе"
        support_line = self._support_line(asset, pressure, city_phrase, rng)
        problem_line = self._problem_line(pressure)

        template = rng.choice(FIRST_TOUCH_STRUCTURES)
        text = template.format(
            hello=hello,
            name_part=f"{name}, " if name else "",
            item=item,
            price_part=f" за {item_price}" if item_price else "",
            problem_line=problem_line,
            support_line=support_line.replace("БФЛ", company_name),
            pressure=pressure,
        )
        text = text.replace("компания БФЛ", f"компания {company_name}")
        text = re.sub(r"\s+", " ", text).strip()

        msg_hash = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
        return text, msg_hash

    # Backward-compatible alias used by tests and current scripts.
    def first_message(
        self,
        name: str = "",
        item: str = "товар",
        item_price: str = "",
        city: str = "вашем городе",
    ) -> tuple[str, str]:
        return self.first_touch_for_lead(
            name=name,
            item=item,
            item_price=item_price,
            city=city,
        )

    def bot_response(self, key: str) -> str:
        options = BOT_RESPONSE_TEMPLATES.get(key, [f"[{key}]"])
        return random.choice(options)


if __name__ == "__main__":
    rnd = TextRandomizer(seed="lead_123")
    text, h = rnd.first_touch_for_lead(
        name="Иван",
        item="Срочно продам Honda Civic 2019",
        item_price="700 000 руб.",
        city="Казани",
        category="авто",
        description="Срочно, нужны деньги, торг.",
    )
    print(h)
    print(text)
