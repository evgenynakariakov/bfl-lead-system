"""
Streamlit dashboard for BFL lead pipeline.

Run:
    streamlit run dashboard/streamlit_app.py
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import psycopg2
import streamlit as st
from dotenv import load_dotenv


load_dotenv()

st.set_page_config(
    page_title="BFL Lead Dashboard",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
    .block-container {padding-top: 1.2rem;}
    .stMetric {
        background: linear-gradient(140deg, #151827 0%, #1d2335 100%);
        border: 1px solid #2a324a;
        border-radius: 12px;
        padding: 10px;
    }
</style>
""",
    unsafe_allow_html=True,
)


def _db_params() -> dict:
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": os.getenv("DB_PORT", "5432"),
        "dbname": os.getenv("DB_NAME", "bfl_leads"),
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASSWORD", ""),
    }


@st.cache_data(ttl=30)
def load_data_from_db() -> tuple[dict | None, str | None]:
    try:
        conn = psycopg2.connect(**_db_params())
    except Exception as exc:
        return None, f"Не удалось подключиться к БД: {exc}"

    try:
        def _query_df(sql: str) -> pd.DataFrame:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            cur.close()
            return pd.DataFrame(rows, columns=cols)

        leads = _query_df(
            """
            SELECT id, source, city, ad_title, ad_category,
                   ad_price, score, priority, status,
                   debt_amount, has_income, has_property,
                   created_at, contact_attempts, phone
            FROM leads
            ORDER BY score DESC, created_at DESC
            """,
        )

        # Views may not exist in some environments, fallback to computed datasets.
        try:
            funnel = _query_df("SELECT * FROM funnel_stats")
        except Exception:
            funnel = (
                leads.groupby("status", as_index=False)
                .agg(count=("id", "count"), avg_score=("score", "mean"))
                .sort_values("count", ascending=False)
            )

        try:
            daily = _query_df("SELECT * FROM daily_stats LIMIT 30")
        except Exception:
            daily = (
                leads.assign(day=pd.to_datetime(leads["created_at"]).dt.date)
                .groupby("day", as_index=False)
                .agg(
                    total_leads=("id", "count"),
                    qualified=("status", lambda s: (s == "qualified").sum()),
                    handed_over=("status", lambda s: (s == "handed_over").sum()),
                    avg_score=("score", "mean"),
                )
                .sort_values("day", ascending=False)
                .head(30)
            )

        try:
            cities = _query_df("SELECT * FROM leads_by_city")
        except Exception:
            cities = (
                leads.groupby("city", as_index=False)
                .agg(
                    total=("id", "count"),
                    high_score=("score", lambda s: (s >= 60).sum()),
                    qualified=("status", lambda s: (s == "qualified").sum()),
                    avg_score=("score", "mean"),
                )
                .sort_values("total", ascending=False)
            )

        runs = _query_df(
            """
            SELECT city, search_query, total_found, new_leads, status, started_at
            FROM parse_runs
            ORDER BY started_at DESC
            LIMIT 20
            """,
        )

        return {
            "leads": leads,
            "funnel": funnel,
            "daily": daily,
            "cities": cities,
            "runs": runs,
        }, None
    except Exception as exc:
        return None, f"Ошибка загрузки данных: {exc}"
    finally:
        conn.close()


def generate_demo_data() -> dict:
    np.random.seed(42)
    n = 240

    leads = pd.DataFrame(
        {
            "id": range(1, n + 1),
            "source": "avito",
            "city": np.random.choice(
                ["moskva", "spb", "kazan", "ekb", "novosibirsk", "krasnodar"],
                n,
                p=[0.30, 0.20, 0.12, 0.12, 0.14, 0.12],
            ),
            "ad_title": np.random.choice(
                [
                    "Срочно продам авто",
                    "Продам iPhone, торг",
                    "Срочная продажа мебели",
                    "Продам ноутбук",
                    "Продаю квартиру, срочно",
                ],
                n,
            ),
            "ad_category": np.random.choice(["авто", "электроника", "недвижимость"], n),
            "ad_price": np.random.randint(30_000, 2_500_000, n),
            "score": np.random.randint(15, 100, n),
            "priority": np.random.choice(["low", "medium", "high", "vip"], n, p=[0.3, 0.35, 0.25, 0.1]),
            "status": np.random.choice(
                ["new", "contacted", "responded", "in_bot", "qualified", "rejected", "handed_over"],
                n,
                p=[0.35, 0.22, 0.14, 0.11, 0.09, 0.06, 0.03],
            ),
            "debt_amount": np.random.choice([None, 300_000, 500_000, 900_000, 1_500_000], n),
            "has_income": np.random.choice([True, False, None], n),
            "has_property": np.random.choice([True, False, None], n),
            "contact_attempts": np.random.randint(0, 5, n),
            "phone": np.where(np.random.rand(n) > 0.45, "+7 9XX XXX XX XX", None),
            "created_at": [datetime.now() - timedelta(days=np.random.randint(0, 30)) for _ in range(n)],
        }
    )

    funnel = (
        leads.groupby("status", as_index=False)
        .agg(count=("id", "count"), avg_score=("score", "mean"))
        .sort_values("count", ascending=False)
    )

    daily = (
        leads.assign(day=pd.to_datetime(leads["created_at"]).dt.date)
        .groupby("day", as_index=False)
        .agg(
            total_leads=("id", "count"),
            qualified=("status", lambda s: (s == "qualified").sum()),
            handed_over=("status", lambda s: (s == "handed_over").sum()),
            avg_score=("score", "mean"),
        )
        .sort_values("day", ascending=False)
        .head(30)
    )

    cities = (
        leads.groupby("city", as_index=False)
        .agg(
            total=("id", "count"),
            high_score=("score", lambda s: (s >= 60).sum()),
            qualified=("status", lambda s: (s == "qualified").sum()),
            avg_score=("score", "mean"),
        )
        .sort_values("total", ascending=False)
    )

    runs = pd.DataFrame(
        {
            "city": ["moskva", "spb", "kazan"],
            "search_query": ["продам авто срочно", "срочная продажа", "нужны деньги"],
            "total_found": [57, 44, 26],
            "new_leads": [16, 12, 8],
            "status": ["done", "done", "done"],
            "started_at": [datetime.now() - timedelta(hours=i * 3) for i in range(3)],
        }
    )

    return {"leads": leads, "funnel": funnel, "daily": daily, "cities": cities, "runs": runs}


def _safe_str_series(series: pd.Series) -> pd.Series:
    return series.astype(str).fillna("")


def render_dashboard(data: dict, source_label: str) -> None:
    leads = data["leads"].copy()

    st.title("BFL Lead Dashboard")
    st.caption(source_label)

    with st.sidebar:
        st.title("🎯 BFL")
        st.markdown("---")
        priority_filter = st.multiselect(
            "Приоритет",
            ["vip", "high", "medium", "low"],
            default=["vip", "high", "medium", "low"],
        )
        city_filter = st.text_input("Город (поиск)", "")
        min_score = st.slider("Мин. скор", 0, 100, 40)
        st.markdown("---")
        if st.button("Обновить данные"):
            st.cache_data.clear()
            st.rerun()

    if priority_filter:
        leads = leads[leads["priority"].isin(priority_filter)]
    if city_filter:
        leads = leads[_safe_str_series(leads["city"]).str.contains(city_filter, case=False, na=False)]
    leads = leads[leads["score"] >= min_score]

    total = len(data["leads"])
    qualified = int((data["leads"]["status"] == "qualified").sum())
    handed = int((data["leads"]["status"] == "handed_over").sum())
    avg_score = float(data["leads"]["score"].mean()) if total else 0.0
    cpl = 1_000_000 / max(1, qualified + handed)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Всего лидов", f"{total:,}")
    c2.metric("Qualified", f"{qualified:,}")
    c3.metric("Handed over", f"{handed:,}")
    c4.metric("Средний скор", f"{avg_score:.1f} / 100")
    c5.metric("Прогноз CPL", f"{cpl:,.0f} ₽")

    left, right = st.columns([1, 2])
    with left:
        st.subheader("Воронка")
        funnel = data["funnel"].copy()
        fig_funnel = go.Figure(
            go.Funnel(
                y=funnel["status"],
                x=funnel["count"],
                textinfo="value+percent initial",
            )
        )
        fig_funnel.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=10))
        st.plotly_chart(fig_funnel, width="stretch")

    with right:
        st.subheader("Динамика")
        daily = data["daily"].copy()
        daily["day"] = pd.to_datetime(daily["day"])
        fig_daily = go.Figure()
        fig_daily.add_trace(go.Bar(x=daily["day"], y=daily["total_leads"], name="Лиды"))
        fig_daily.add_trace(go.Bar(x=daily["day"], y=daily["qualified"], name="Qualified"))
        fig_daily.add_trace(
            go.Scatter(
                x=daily["day"],
                y=daily["avg_score"],
                mode="lines+markers",
                name="Avg score",
                yaxis="y2",
            )
        )
        fig_daily.update_layout(
            barmode="group",
            yaxis2=dict(overlaying="y", side="right", showgrid=False),
            height=380,
            margin=dict(l=0, r=0, t=10, b=10),
        )
        st.plotly_chart(fig_daily, width="stretch")

    b1, b2 = st.columns(2)
    with b1:
        st.subheader("По городам")
        cities = data["cities"].copy()
        fig_cities = px.bar(
            cities.sort_values("total", ascending=True).tail(10),
            x="total",
            y="city",
            orientation="h",
            color="avg_score",
        )
        fig_cities.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=10))
        st.plotly_chart(fig_cities, width="stretch")

    with b2:
        st.subheader("Распределение score")
        fig_hist = px.histogram(
            data["leads"],
            x="score",
            nbins=20,
            color="priority",
            color_discrete_map={"vip": "#ef4444", "high": "#f97316", "medium": "#eab308", "low": "#6b7280"},
        )
        fig_hist.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=10))
        st.plotly_chart(fig_hist, width="stretch")

    st.subheader("Лиды (фильтр)")
    table = leads[
        ["id", "city", "ad_title", "ad_category", "score", "priority", "status", "ad_price", "phone", "created_at"]
    ].copy()
    table["ad_price"] = table["ad_price"].map(lambda x: f"{x:,.0f} ₽".replace(",", " "))
    table["created_at"] = pd.to_datetime(table["created_at"])
    st.dataframe(table, width="stretch", height=420)

    with st.expander("История запусков парсера"):
        st.dataframe(data["runs"], width="stretch")


def main() -> None:
    with st.sidebar:
        st.checkbox("Demo режим (без БД)", key="demo_mode", value=True)
        st.checkbox("Auto fallback в Demo при ошибке БД", key="auto_demo", value=True)

    use_demo = st.session_state.get("demo_mode", True)
    auto_demo = st.session_state.get("auto_demo", True)

    if use_demo:
        data = generate_demo_data()
        render_dashboard(data, "Источник: Demo data")
        return

    data, error = load_data_from_db()
    if data is None:
        st.error(error or "Ошибка подключения к БД")
        if auto_demo:
            st.warning("Переключено на Demo-данные автоматически.")
            render_dashboard(generate_demo_data(), "Источник: Demo data (fallback)")
            return
        st.stop()

    render_dashboard(data, "Источник: PostgreSQL")


if __name__ == "__main__":
    main()
