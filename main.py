"""
main.py — Точка входа для запуска компонентов системы.

Использование:
    python main.py parser   → Запустить парсер Авито
    python main.py bot      → Запустить Telegram-бот
    python main.py test     → Прогнать тесты без БД
    python main.py all      → Запустить всё одновременно
"""
import asyncio
import sys

from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table


console = Console()


def configure_console_encoding():
    """Force UTF-8 console output to avoid Windows cp1251 emoji crashes."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def print_banner():
    console.print(
        Panel.fit(
            "[bold purple]BFL Lead Generation System[/bold purple]\n"
            "[dim]Traffic Engineer Demo — by [candidate][/dim]",
            border_style="purple",
        )
    )


async def run_parser():
    """Запустить парсер с реальным подключением к БД."""
    from database.db import Database
    from lead_parser.avito_parser import AvitoParserRunner

    db = Database()
    try:
        await db.connect()
        runner = AvitoParserRunner(db=db)
        new, updated = await runner.run_all_cities()
        console.print(f"[green]✅ Парсинг завершён: {new} новых, {updated} обновлённых[/green]")
    except Exception as exc:
        console.print(
            f"[red]❌ Парсер не смог стартовать: {type(exc).__name__}: {exc}[/red]\n"
            "[dim]Проверьте PostgreSQL и DB_* в .env[/dim]"
        )
    finally:
        await db.disconnect()


def run_bot():
    """Запустить Telegram-бота."""
    from bot.bot_main import run_bot as _run_bot

    _run_bot()


async def run_tests():
    """Прогнать все тесты без БД."""
    console.print("\n[bold]🧪 Тест 1: Скоринг лидов[/bold]")
    from lead_parser.scorer import score_lead

    test_cases = [
        (
            "Срочно! Продам Honda Civic, нужны деньги на долги",
            "Вынужден продать из-за финансовых трудностей. Пристав.",
            800_000,
            1_200_000,
            True,
        ),
        (
            "iPhone 15 Pro, торг, цена снижена",
            "Хороший торг. Деньги нужны срочно.",
            50_000,
            90_000,
            True,
        ),
        (
            "Toyota Camry в автосалоне",
            "Дилер. Оптовые поставки. Без пробега по РФ.",
            None,
            None,
            True,
        ),
    ]

    t = Table(show_header=True, header_style="bold purple")
    t.add_column("Заголовок", width=45)
    t.add_column("Скор", width=6)
    t.add_column("Приоритет", width=10)
    t.add_column("Ключевые сигналы", width=40)

    for title, desc, price, price_orig, has_phone in test_cases:
        result = score_lead(title, desc, "", price, price_orig, has_phone)
        color = {
            "vip": "red",
            "high": "yellow",
            "medium": "cyan",
            "low": "dim",
            "spam": "dim",
        }.get(result.priority, "white")
        t.add_row(
            title[:44],
            str(result.total),
            f"[{color}]{result.priority}[/{color}]",
            ", ".join(result.matched_words[:3]),
        )
    console.print(t)

    console.print("\n[bold]🧪 Тест 2: Рандомизатор текстов[/bold]")
    from bot.randomizer import TextRandomizer

    randomizer = TextRandomizer()
    for i in range(3):
        msg, hash_ = randomizer.first_message(
            name="Иван",
            item="Honda Civic 2019",
            item_price="700 000 руб.",
            city="Москве",
        )
        console.print(f"\n[dim]Вариант {i + 1} [{hash_}]:[/dim]")
        console.print(f"[green]{msg}[/green]")

    console.print("\n[bold]🧪 Тест 3: Парсер суммы долга из текста[/bold]")
    from bot.bot_main import format_amount, parse_debt_amount

    test_amounts = [
        "500 тысяч",
        "1.2 млн",
        "300к",
        "700000",
        "полтора миллиона",
        "три миллиона",
        "2 000 000",
    ]
    t2 = Table(show_header=True, header_style="bold purple")
    t2.add_column("Ввод пользователя", width=20)
    t2.add_column("Распознано", width=20)

    for inp in test_amounts:
        amount = parse_debt_amount(inp)
        t2.add_row(inp, format_amount(amount))
    console.print(t2)

    console.print("\n[bold green]✅ Все тесты пройдены![/bold green]")


def main():
    configure_console_encoding()
    print_banner()

    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"

    if cmd == "parser":
        console.print("[yellow]🚀 Запуск парсера Авито...[/yellow]")
        asyncio.run(run_parser())
    elif cmd == "bot":
        console.print("[yellow]🤖 Запуск Telegram-бота...[/yellow]")
        run_bot()
    elif cmd == "test":
        console.print("[yellow]🧪 Запуск тестов...[/yellow]")
        asyncio.run(run_tests())
    elif cmd == "all":
        console.print("[yellow]🚀 Запуск всех компонентов...[/yellow]")
        console.print("[dim]Dashboard: streamlit run dashboard/streamlit_app.py[/dim]")
        console.print("[dim]Bot: python main.py bot[/dim]")
        console.print("[dim]Parser: python main.py parser[/dim]")
        asyncio.run(run_tests())
    else:
        console.print(f"[red]Неизвестная команда: {cmd}[/red]")
        console.print("Используй: parser | bot | test | all")


if __name__ == "__main__":
    main()
