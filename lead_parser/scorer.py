"""
parser/scorer.py — Скоринг лидов.

Система присваивает каждому объявлению балл от 0 до 100
на основе сигналов финансового стресса. Чем выше балл,
тем вероятнее что у человека есть долг >300k.

Логика:
  score = urgency + keywords + category + price_drop + contact
  priority: vip(80+) / high(60-79) / medium(40-59) / low(<40)
"""
import re
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────
#  Словари сигналов финансового стресса
# ─────────────────────────────────────────────────────────────

# Срочность — самый сильный сигнал. "Срочно" + "ниже рынка" = человек в беде.
URGENCY_KEYWORDS = {
    # Вес 15
    15: [
        r"срочно", r"очень срочно", r"срочная продажа", r"нужны деньги",
        r"деньги нужны", r"финансовые трудности", r"долги", r"долг",
        r"кредит(ы|ов)?", r"займ", r"мфо", r"коллектор",
        r"приставы?", r"фссп", r"суд", r"арест",
    ],
    # Вес 10
    10: [
        r"торг", r"торгуемся", r"хороший торг", r"уступлю",
        r"без торга не предлагать",  # парадокс: люди пишут так, но торгуются
        r"цена снижена", r"снизил цену", r"скидка",
        r"вынужден продать", r"обстоятельства",
        r"переезд", r"развод", r"расходимся",
    ],
    # Вес 5
    5: [
        r"продам быстро", r"быстро", r"не откладывайте",
        r"сегодня", r"завтра", r"в течение дня",
        r"рассмотрю предложения",
    ],
}

# Категории — что чаще всего продают люди с долгами
CATEGORY_SCORES = {
    "авто":         25,  # Продажа авто — самый частый сигнал
    "мото":         20,
    "недвижимость": 20,  # Продают квартиру/дачу
    "электроника":  15,  # Телефоны, ноутбуки — быстрые деньги
    "бытовая_техника": 12,
    "мебель":       10,
    "одежда":        5,
    "другое":        3,
}

# Маппинг заголовков Авито → наши категории
CATEGORY_MAP = {
    "авто": ["авто", "машин", "автомобил", "honda", "toyota", "kia", "hyundai",
             "bmw", "mercedes", "lada", "vaz", "nissan", "ford", "renault",
             "volkswagen", "skoda", "mazda", "audi", "volvo", "subaru"],
    "мото": ["мотоцикл", "мопед", "скутер", "квадроцикл"],
    "недвижимость": ["квартир", "комнат", "дом", "дача", "участок", "гараж"],
    "электроника": ["iphone", "samsung", "телефон", "смартфон", "ноутбук",
                    "планшет", "macbook", "ipad", "playstation", "xbox"],
    "бытовая_техника": ["стиральн", "холодильник", "телевизор", "пылесос",
                         "посудомоечн", "микроволн"],
    "мебель": ["диван", "кровать", "шкаф", "стол", "кресло", "мебель"],
}

# Слова которые СНИЖАЮТ скор (спамеры, перекупщики, не ЦА)
NEGATIVE_KEYWORDS = [
    r"перекуп", r"автосалон", r"дилер", r"оптом", r"партия",
    r"производство", r"новый .{0,20} в наличии",
    r"без пробега по рф",  # продавец-перекуп
]


# ─────────────────────────────────────────────────────────────
#  Датакласс результата скоринга
# ─────────────────────────────────────────────────────────────

@dataclass
class ScoreResult:
    total:          int   = 0
    urgency:        int   = 0
    keywords:       int   = 0
    category:       int   = 0
    price_drop:     int   = 0
    has_phone:      int   = 0
    negative:       int   = 0
    priority:       str   = "low"
    matched_words:  list  = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total":         self.total,
            "urgency":       self.urgency,
            "keywords":      self.keywords,
            "category":      self.category,
            "price_drop":    self.price_drop,
            "has_phone":     self.has_phone,
            "negative":      self.negative,
            "priority":      self.priority,
            "matched_words": self.matched_words,
        }


# ─────────────────────────────────────────────────────────────
#  Главная функция скоринга
# ─────────────────────────────────────────────────────────────

def score_lead(
    title: str,
    description: str = "",
    category_raw: str = "",
    price: Optional[float] = None,
    price_original: Optional[float] = None,
    has_phone: bool = False,
) -> ScoreResult:
    """
    Рассчитать скоринговый балл лида.

    Args:
        title:          Заголовок объявления
        description:    Текст объявления
        category_raw:   Категория как написана на Авито
        price:          Текущая цена
        price_original: Начальная цена (если снизили)
        has_phone:      Есть ли номер телефона

    Returns:
        ScoreResult с разбивкой по компонентам и итоговым приоритетом
    """
    result = ScoreResult()

    # Объединяем всё для поиска
    full_text = f"{title} {description}".lower()

    # ── 1. Проверяем негативные сигналы ──────────────────────
    for pattern in NEGATIVE_KEYWORDS:
        if re.search(pattern, full_text, re.IGNORECASE):
            result.negative -= 30
            result.matched_words.append(f"[-] {pattern}")

    # Если перекупщик/дилер — дальше не считаем
    if result.negative <= -30:
        result.total = 0
        result.priority = "spam"
        return result

    # ── 2. Срочность (urgency) ────────────────────────────────
    for weight, patterns in URGENCY_KEYWORDS.items():
        for pattern in patterns:
            if re.search(pattern, full_text, re.IGNORECASE):
                result.urgency = min(result.urgency + weight, 40)
                result.matched_words.append(f"[+{weight}] {pattern}")

    # ── 3. Категория ──────────────────────────────────────────
    detected_category = _detect_category(title, category_raw)
    result.category = CATEGORY_SCORES.get(detected_category, 3)
    result.matched_words.append(f"[cat] {detected_category}")

    # ── 4. Снижение цены (признак давления) ───────────────────
    if price and price_original and price_original > 0:
        drop_pct = (price_original - price) / price_original * 100
        if drop_pct >= 30:
            result.price_drop = 15
            result.matched_words.append(f"[price_drop] -{drop_pct:.0f}%")
        elif drop_pct >= 15:
            result.price_drop = 8
            result.matched_words.append(f"[price_drop] -{drop_pct:.0f}%")
        elif drop_pct >= 5:
            result.price_drop = 3

    # ── 5. Наличие телефона ───────────────────────────────────
    result.has_phone = 10 if has_phone else 0

    # ── 6. Итог ───────────────────────────────────────────────
    raw = (
        result.urgency
        + result.category
        + result.price_drop
        + result.has_phone
        + result.negative
    )
    result.total = max(0, min(100, raw))

    # Приоритет
    if result.total >= 80:
        result.priority = "vip"
    elif result.total >= 60:
        result.priority = "high"
    elif result.total >= 40:
        result.priority = "medium"
    else:
        result.priority = "low"

    return result


def _detect_category(title: str, category_raw: str) -> str:
    """Определить категорию из заголовка и метки Авито."""
    text = f"{title} {category_raw}".lower()
    for cat, keywords in CATEGORY_MAP.items():
        for kw in keywords:
            if kw in text:
                return cat
    return "другое"


# ─────────────────────────────────────────────────────────────
#  Тест (запустить: python -m parser.scorer)
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_cases = [
        {
            "title": "Срочно! Продам Honda Civic 2019, нужны деньги на долги",
            "description": "Вынужден продать из-за финансовых трудностей. Пристав арестовал счет. Торг уместен.",
            "price": 700_000,
            "price_original": 1_100_000,
            "has_phone": True,
        },
        {
            "title": "Продам iPhone 15 Pro, срочно, цена снижена",
            "description": "Хороший торг. Деньги нужны срочно. Рассмотрю предложения.",
            "price": 50_000,
            "price_original": 90_000,
            "has_phone": True,
        },
        {
            "title": "Toyota Camry 2020 в автосалоне, без пробега по РФ",
            "description": "Оптовые поставки. Новые автомобили от дилера.",
            "price": 2_500_000,
            "has_phone": True,
        },
    ]

    print("=" * 60)
    for case in test_cases:
        r = score_lead(**case)
        print(f"\n📋 {case['title'][:50]}...")
        print(f"   Балл: {r.total} | Приоритет: {r.priority.upper()}")
        print(f"   Срочность: {r.urgency} | Категория: {r.category} | "
              f"Цена↓: {r.price_drop} | Телефон: {r.has_phone}")
        print(f"   Сигналы: {', '.join(r.matched_words[:5])}")
    print("=" * 60)
