-- PioneerNews D1 Database Schema
-- 运行: wrangler d1 execute pioneer-news-db --local --file=./schema.sql

-- 创建新闻表
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    url TEXT,
    source TEXT NOT NULL,
    publish_time TEXT,
    intro TEXT,
    title_hash TEXT UNIQUE,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_publish_time ON news(publish_time DESC);
CREATE INDEX IF NOT EXISTS idx_created_at ON news(created_at ASC);
CREATE INDEX IF NOT EXISTS idx_source ON news(source);
CREATE INDEX IF NOT EXISTS idx_title_hash ON news(title_hash);
