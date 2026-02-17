# BFL Lead System

Это рабочий демо-проект для тестового задания Junior Traffic Engineer.
Логика простая: собираем объявления, считаем score, готовим первое сообщение с deep-link на бота, проводим квалификацию в Telegram, сохраняем результат в PostgreSQL и смотрим в дашборде.

## Что внутри

`main.py` — общий запуск (`parser`, `bot`, `test`, `all`).

`lead_parser/avito_parser.py` — парсинг Авито через Playwright.

`lead_parser/scorer.py` — оценка лида (0..100) и приоритет (`vip`, `high`, `medium`, `low`).

`send_first_touch.py` — выбирает новых лидов, генерирует первое сообщение и сохраняет результат в БД.

`bot/bot_main.py` — Telegram-бот (FSM) для квалификации.

`bot/randomizer.py` — генерация текстов первого касания и ответов бота.

`dashboard/streamlit_app.py` — визуализация воронки и таблиц.

`database/db.py` — методы для записи/чтения данных.

`database/schema.sql` — схема БД.

`seed_test_data.py` — генерация тестовых лидов для демо.

`add_manual_lead.py` — ручное добавление одного лида.

## Требования

- Python 3.13
- PostgreSQL 14+
- Windows PowerShell

## Установка

```powershell
cd D:\bfl_system_complete
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

## Настройка .env

Скопируйте шаблон и заполните значения:

```powershell
Copy-Item .env.example .env
notepad .env
```

## База данных

Если `psql` не добавлен в PATH, используйте полный путь к `psql.exe`.

```powershell
$PSQL = "D:\Games-EXE\postgres\bin\psql.exe"
& $PSQL -U postgres -c "CREATE DATABASE bfl_leads;"
& $PSQL -U postgres -d bfl_leads -f "D:\bfl_system_complete\database\schema.sql"
```

Если БД уже есть, выполните только вторую команду.

## Базовые команды

```powershell
python main.py test
python main.py parser
python send_first_touch.py
python main.py bot
streamlit run dashboard/streamlit_app.py
```

## Рекомендуемый порядок для демо

1. Наполнить БД тестовыми данными:

```powershell
python seed_test_data.py --wipe --count 60
```

2. Сгенерировать первое касание:

```powershell
python send_first_touch.py
```

3. Запустить бота:

```powershell
python main.py bot
```

4. Взять `deep_link` из таблицы `leads`, открыть ссылку и пройти сценарий в Telegram.

5. Открыть дашборд:

```powershell
streamlit run dashboard/streamlit_app.py
```

## Что проверять в БД

После `send_first_touch.py` в `leads` должны быть заполнены:

- `deep_link`
- `outreach_status`
- `first_touch_text`
- `first_touch_at`

После прохождения бота:

- в `bot_sessions` появляется запись с `telegram_chat_id`, `lead_id`, `current_step`, `session_data`
- в `leads` обновляются `debt_amount`, `has_income`, `has_property`, `creditors_count`, `qualification_data`, `status`

Проверка:

```sql
SELECT id, status, outreach_status, deep_link, first_touch_at
FROM leads
ORDER BY id DESC
LIMIT 20;

SELECT id, telegram_chat_id, lead_id, current_step
FROM bot_sessions
ORDER BY id DESC
LIMIT 20;
```

## Как работает скоринг

`lead_parser/scorer.py` считает итоговый балл из нескольких сигналов:

- срочность и стрессовые слова в тексте
- категория объявления
- снижение цены относительно старой цены
- наличие телефона
- отрицательные признаки (спам, перекуп, салон)

Итог ограничивается диапазоном 0..100.
Приоритет:

- `vip`: 80+
- `high`: 60-79
- `medium`: 40-59
- `low`: 0-39

## Частые проблемы

`send_first_touch.py` пишет "Нет лидов для первого касания".
Значит нет записей под фильтр: `status='new'`, `outreach_status='new'`, `score >= OUTREACH_MIN_SCORE`.
Решение: `seed_test_data.py` или снизить `OUTREACH_MIN_SCORE`.

`bot_sessions` пустая.
Обычно бот стартовали без payload. Для ручной проверки отправьте:

```text
/start lead_<id>
```

Парсер Авито ловит капчу.
Это нормальная ситуация. Для демонстрации стабильнее использовать `seed_test_data.py`.

## Ограничение текущей версии

`send_first_touch.py` пока сохраняет факт первого касания только в БД.
Реальная отправка в Avito/WhatsApp/TG API не включена и добавляется отдельным модулем.
