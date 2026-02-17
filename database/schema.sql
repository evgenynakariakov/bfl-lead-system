\encoding UTF8

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS leads (
    id                  SERIAL PRIMARY KEY,
    source              VARCHAR(50)  NOT NULL DEFAULT 'avito',
    avito_id            VARCHAR(50)  UNIQUE,
    avito_url           TEXT,
    city                VARCHAR(100),
    ad_title            TEXT,
    ad_description      TEXT,
    ad_category         VARCHAR(100),
    ad_price            NUMERIC(12, 2),
    ad_price_original   NUMERIC(12, 2),
    seller_name         VARCHAR(200),
    phone               VARCHAR(30),
    score               INTEGER      DEFAULT 0,
    score_breakdown     JSONB,
    priority            VARCHAR(20)  DEFAULT 'low',
    status              VARCHAR(50)  DEFAULT 'new',
    debt_amount         NUMERIC(15, 2),
    has_income          BOOLEAN,
    has_property        BOOLEAN,
    creditors_count     INTEGER,
    qualification_data  JSONB,
    telegram_chat_id    BIGINT,
    telegram_username   VARCHAR(100),
    deep_link           TEXT,
    outreach_status     VARCHAR(32) DEFAULT 'new',
    first_touch_text    TEXT,
    first_touch_at      TIMESTAMP,
    contact_attempts    INTEGER      DEFAULT 0,
    last_contact_at     TIMESTAMP,
    created_at          TIMESTAMP    DEFAULT NOW(),
    updated_at          TIMESTAMP    DEFAULT NOW(),
    notes               TEXT
);

CREATE INDEX IF NOT EXISTS idx_leads_status    ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_score     ON leads(score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_city      ON leads(city);
CREATE INDEX IF NOT EXISTS idx_leads_created   ON leads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_tg_chat   ON leads(telegram_chat_id);
CREATE INDEX IF NOT EXISTS idx_leads_priority  ON leads(priority);
CREATE INDEX IF NOT EXISTS idx_leads_outreach  ON leads(outreach_status, score DESC);

ALTER TABLE leads ADD COLUMN IF NOT EXISTS deep_link TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS outreach_status VARCHAR(32) DEFAULT 'new';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS first_touch_text TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS first_touch_at TIMESTAMP;

CREATE TABLE IF NOT EXISTS bot_sessions (
    id               SERIAL PRIMARY KEY,
    telegram_chat_id BIGINT       NOT NULL UNIQUE,
    lead_id          INTEGER      REFERENCES leads(id) ON DELETE CASCADE,
    current_step     VARCHAR(100) DEFAULT 'start',
    session_data     JSONB        DEFAULT '{}',
    started_at       TIMESTAMP    DEFAULT NOW(),
    updated_at       TIMESTAMP    DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_chat ON bot_sessions(telegram_chat_id);

CREATE TABLE IF NOT EXISTS messages (
    id          SERIAL PRIMARY KEY,
    lead_id     INTEGER     REFERENCES leads(id) ON DELETE CASCADE,
    channel     VARCHAR(20) DEFAULT 'telegram',
    direction   VARCHAR(10) NOT NULL,
    text        TEXT,
    sent_at     TIMESTAMP   DEFAULT NOW(),
    delivered   BOOLEAN     DEFAULT FALSE,
    read_at     TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_lead ON messages(lead_id);

CREATE TABLE IF NOT EXISTS parse_runs (
    id              SERIAL PRIMARY KEY,
    started_at      TIMESTAMP   DEFAULT NOW(),
    finished_at     TIMESTAMP,
    source          VARCHAR(50) DEFAULT 'avito',
    city            VARCHAR(100),
    search_query    TEXT,
    total_found     INTEGER     DEFAULT 0,
    new_leads       INTEGER     DEFAULT 0,
    updated_leads   INTEGER     DEFAULT 0,
    status          VARCHAR(20) DEFAULT 'running',
    error_message   TEXT
);

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_leads_updated ON leads;
CREATE TRIGGER trg_leads_updated
    BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS trg_sessions_updated ON bot_sessions;
CREATE TRIGGER trg_sessions_updated
    BEFORE UPDATE ON bot_sessions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE OR REPLACE VIEW funnel_stats AS
SELECT
    status,
    COUNT(*) AS count,
    ROUND(AVG(score), 1) AS avg_score,
    ROUND(COUNT(*) * 100.0 / NULLIF(SUM(COUNT(*)) OVER (), 0), 1) AS pct
FROM leads
GROUP BY status
ORDER BY
    CASE status
        WHEN 'new'          THEN 1
        WHEN 'contacted'    THEN 2
        WHEN 'responded'    THEN 3
        WHEN 'in_bot'       THEN 4
        WHEN 'qualified'    THEN 5
        WHEN 'handed_over'  THEN 6
        WHEN 'rejected'     THEN 7
        ELSE 8
    END;

CREATE OR REPLACE VIEW daily_stats AS
SELECT
    DATE(created_at)                                   AS day,
    COUNT(*)                                           AS total_leads,
    COUNT(*) FILTER (WHERE status = 'qualified')       AS qualified,
    COUNT(*) FILTER (WHERE status = 'handed_over')     AS handed_over,
    ROUND(AVG(score), 1)                               AS avg_score
FROM leads
GROUP BY DATE(created_at)
ORDER BY day DESC;

CREATE OR REPLACE VIEW leads_by_city AS
SELECT
    city,
    COUNT(*)                                     AS total,
    COUNT(*) FILTER (WHERE score >= 60)          AS high_score,
    COUNT(*) FILTER (WHERE status = 'qualified') AS qualified,
    ROUND(AVG(score), 1)                         AS avg_score
FROM leads
WHERE city IS NOT NULL
GROUP BY city
ORDER BY total DESC;
