import ssl
import os
import io
import re
import time
import json
import html
import hashlib
import asyncio
import sqlite3
import logging
import tracemalloc
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from contextlib import asynccontextmanager
from collections import Counter

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

if os.environ.get("TRACE_MALLOC"):
    import tracemalloc

    tracemalloc.start()

_re_highlight_cache: dict[str, re.Pattern] = {}


def _get_highlight_pattern(query: str) -> re.Pattern:
    if query not in _re_highlight_cache:
        _re_highlight_cache[query] = re.compile(re.escape(query), re.IGNORECASE)
    return _re_highlight_cache[query]


TZ_BJ = timezone(timedelta(hours=8))


def now_bj() -> datetime:
    return datetime.now(TZ_BJ).replace(tzinfo=None)


def ts_from_utc(ts: int) -> int:
    return ts


def ts_from_bj_str(s: str) -> int:
    try:
        dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        return int(dt.replace(tzinfo=TZ_BJ).timestamp())
    except (ValueError, TypeError):
        return 0


def bj_str_from_ts(ts: int) -> str:
    if not ts:
        return now_bj().strftime("%Y-%m-%d %H:%M:%S")
    return datetime.fromtimestamp(ts, tz=TZ_BJ).strftime("%Y-%m-%d %H:%M:%S")


def parse_relative_time(time_str: str) -> int:
    """解析相对时间字符串，如 '5分钟前', '2小时前', '昨天 23:05', '今天 22:58', '3天前', '05-16 14:30'"""
    now = now_bj()
    if not time_str:
        return 0
    try:
        if "分钟前" in time_str:
            m = re.search(r"(\d+)", time_str)
            if m:
                return int(
                    (now - timedelta(minutes=int(m.group(1))))
                    .replace(tzinfo=TZ_BJ)
                    .timestamp()
                )
        elif "小时前" in time_str:
            m = re.search(r"(\d+)", time_str)
            if m:
                return int(
                    (now - timedelta(hours=int(m.group(1))))
                    .replace(tzinfo=TZ_BJ)
                    .timestamp()
                )
        elif "天前" in time_str:
            m = re.search(r"(\d+)", time_str)
            if m:
                return int(
                    (now - timedelta(days=int(m.group(1))))
                    .replace(tzinfo=TZ_BJ)
                    .timestamp()
                )
        # 处理 "昨天 HH:MM" 格式
        elif time_str.startswith("昨天"):
            m = re.search(r"(\d{1,2}):(\d{2})", time_str)
            if m:
                hour, minute = int(m.group(1)), int(m.group(2))
                dt = (now - timedelta(days=1)).replace(
                    hour=hour, minute=minute, second=0
                )
                return int(dt.replace(tzinfo=TZ_BJ).timestamp())
            else:
                return int(
                    (now - timedelta(days=1))
                    .replace(hour=0, minute=0, second=0, tzinfo=TZ_BJ)
                    .timestamp()
                )
        # 处理 "今天 HH:MM" 格式
        elif time_str.startswith("今天"):
            m = re.search(r"(\d{1,2}):(\d{2})", time_str)
            if m:
                hour, minute = int(m.group(1)), int(m.group(2))
                dt = now.replace(hour=hour, minute=minute, second=0)
                return int(dt.replace(tzinfo=TZ_BJ).timestamp())
        elif "前天" in time_str:
            return int(
                (now - timedelta(days=2))
                .replace(hour=0, minute=0, second=0, tzinfo=TZ_BJ)
                .timestamp()
            )
        # 尝试解析 "MM-DD HH:MM" 格式
        m = re.match(r"^(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})$", time_str)
        if m:
            month, day, hour, minute = (
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
                int(m.group(4)),
            )
            dt = now.replace(month=month, day=day, hour=hour, minute=minute, second=0)
            return int(dt.replace(tzinfo=TZ_BJ).timestamp())
    except (ValueError, AttributeError):
        pass
    return 0


def compute_simhash(text: str) -> int:
    if not text:
        return 0
    try:
        import jieba

        words = jieba.cut(text)
        from collections import Counter

        word_freq = Counter(words)
        v = [0] * 64
        for word, freq in word_freq.items():
            if not word.strip():
                continue
            word_hash = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
            for i in range(64):
                if word_hash & (1 << i):
                    v[i] += freq
                else:
                    v[i] -= freq
        fingerprint = 0
        for i in range(64):
            if v[i] > 0:
                fingerprint |= 1 << i
        return fingerprint
    except ImportError:
        ngrams = []
        n = 3
        for i in range(len(text) - n + 1):
            ngrams.append(text[i : i + n])
        if not ngrams:
            ngrams = [text]
        v = [0] * 64
        for ng in ngrams:
            ng_hash = int(hashlib.md5(ng.encode("utf-8")).hexdigest(), 16)
            for i in range(64):
                if ng_hash & (1 << i):
                    v[i] += 1
                else:
                    v[i] -= 1
        fingerprint = 0
        for i in range(64):
            if v[i] > 0:
                fingerprint |= 1 << i
        return fingerprint


def hamming_distance(hash1: int, hash2: int) -> int:
    x = hash1 ^ hash2
    dist = 0
    while x:
        dist += 1
        x &= x - 1
    return dist


def compute_title_full_hash(title: str) -> str:
    return hashlib.md5(title.encode("utf-8")).hexdigest()


def compute_url_hash(url: str) -> str:
    if not url or url == "#":
        return ""
    return hashlib.md5(url.encode("utf-8")).hexdigest()


# GDELT 需要忽略 SSL 验证
gdelt_ssl_context = ssl.create_default_context()
gdelt_ssl_context.check_hostname = False
gdelt_ssl_context.verify_mode = ssl.CERT_NONE
# 兼容旧版 TLS 配置
gdelt_ssl_context.set_ciphers("DEFAULT:@SECLEVEL=1")

# 按来源的请求速率限制（秒），优先保证不会收到 429
SOURCE_RATE_LIMITS: dict[str, float] = {
    "GDELT": 20.0,  # 免费 API 限制严格，至少间隔 20 秒
}
_last_source_req: dict[str, float] = {}  # 各来源上次请求时间戳


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(_background_fetch_loop())
    asyncio.create_task(_timeline_startup_build())
    yield


app = FastAPI(
    title="财经新闻实时展示", docs_url=None, redoc_url=None, lifespan=lifespan
)
app.mount("/static", StaticFiles(directory="static"), name="static")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news.db")
MAX_DB_SIZE_MB = 500  # 数据库最大 500MB


_db_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db_conn.row_factory = sqlite3.Row
    return _db_conn


@contextmanager
def get_db():
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT,
                source TEXT NOT NULL,
                publish_time TEXT,
                publish_ts INTEGER DEFAULT 0,
                intro TEXT,
                title_hash TEXT UNIQUE,
                created_at TEXT
            )
        """)
        try:
            c.execute("SELECT publish_ts FROM news LIMIT 1")
        except sqlite3.OperationalError:
            c.execute("ALTER TABLE news ADD COLUMN publish_ts INTEGER DEFAULT 0")
        try:
            c.execute("SELECT title_full_hash FROM news LIMIT 1")
        except sqlite3.OperationalError:
            c.execute("ALTER TABLE news ADD COLUMN title_full_hash TEXT")
        try:
            c.execute("SELECT url_hash FROM news LIMIT 1")
        except sqlite3.OperationalError:
            c.execute("ALTER TABLE news ADD COLUMN url_hash TEXT")
        try:
            c.execute("SELECT simhash FROM news LIMIT 1")
        except sqlite3.OperationalError:
            c.execute("ALTER TABLE news ADD COLUMN simhash TEXT")
        try:
            c.execute("SELECT dedup_group FROM news LIMIT 1")
        except sqlite3.OperationalError:
            c.execute("ALTER TABLE news ADD COLUMN dedup_group INTEGER DEFAULT 0")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_publish_ts ON news(publish_ts DESC, id DESC)"
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_created ON news(created_at ASC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_title ON news(title)")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_title_full_hash ON news(title_full_hash)"
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_url_hash ON news(url_hash)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_simhash ON news(simhash)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_dedup_group ON news(dedup_group)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS timeline_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_date TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '社会热点',
                importance INTEGER DEFAULT 2,
                description TEXT,
                source TEXT DEFAULT 'crawler',
                source_url TEXT,
                event_hash TEXT UNIQUE,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.commit()
        yield conn
    except Exception:
        conn.rollback()
        raise


def db_insert_news(news_list):
    if not news_list:
        return [], 0
    with get_db() as conn:
        c = conn.cursor()
        new_hashes = []
        inserted = 0
        c.execute("SELECT MAX(dedup_group) FROM news")
        max_group = c.fetchone()[0] or 0
        seven_days_ago = int(time.time()) - 7 * 86400
        for n in news_list:
            title = n["title"]
            url = n.get("url", "#")
            title_full_hash = compute_title_full_hash(title)
            c.execute(
                "SELECT id FROM news WHERE title_full_hash = ? LIMIT 1",
                (title_full_hash,),
            )
            if c.fetchone():
                logger.info(f"去重[标题精确]: {title[:40]}")
                continue
            url_hash = compute_url_hash(url)
            if url_hash:
                c.execute("SELECT id FROM news WHERE url_hash = ? LIMIT 1", (url_hash,))
                if c.fetchone():
                    logger.info(f"去重[URL精确]: {title[:40]}")
                    continue
            simhash_val = compute_simhash(title)
            simhash_hex = f"{simhash_val:016x}"
            dedup_group = 0
            c.execute(
                "SELECT simhash, dedup_group FROM news WHERE simhash IS NOT NULL AND simhash != '' AND dedup_group > 0 AND publish_ts > ? ORDER BY publish_ts DESC LIMIT 500",
                (seven_days_ago,),
            )
            existing = c.fetchall()
            for ex in existing:
                ex_simhash = (
                    int(ex["simhash"], 16)
                    if isinstance(ex["simhash"], str)
                    else ex["simhash"]
                )
                if hamming_distance(simhash_val, ex_simhash) <= 10:
                    dedup_group = ex["dedup_group"]
                    logger.info(
                        f"去重[SimHash近义]: {title[:40]} -> group {dedup_group}"
                    )
                    break
            if dedup_group == 0:
                max_group += 1
                dedup_group = max_group
            title_hash = f"{n['title'][:30]}|{n['source']}"
            try:
                c.execute(
                    """
                    INSERT OR IGNORE INTO news (title, url, source, publish_time, publish_ts, intro, title_hash, created_at, title_full_hash, url_hash, simhash, dedup_group)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        title,
                        url,
                        n["source"],
                        n["publish_time"],
                        n.get("publish_ts", 0),
                        n["intro"],
                        title_hash,
                        now_bj().strftime("%Y-%m-%d %H:%M:%S"),
                        title_full_hash,
                        url_hash,
                        simhash_hex,
                        dedup_group,
                    ),
                )
                if c.rowcount > 0:
                    new_hashes.append(title_hash)
                    inserted += 1
            except sqlite3.IntegrityError:
                pass
        conn.commit()
    return new_hashes, inserted


def db_search_news(query, limit=10, offset=0):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT n.title, n.url, n.source, n.publish_time, n.publish_ts, n.intro, n.dedup_group,
               COALESCE((SELECT COUNT(*) FROM news n2 WHERE n2.dedup_group = n.dedup_group AND n2.dedup_group > 0), 1) AS dedup_count
            FROM news n
            WHERE instr(lower(n.title), lower(?)) OR instr(lower(n.intro), lower(?)) OR instr(lower(n.source), lower(?))
            ORDER BY n.publish_ts DESC, n.id DESC
            LIMIT ? OFFSET ?
        """,
            (query, query, query, limit, offset),
        )
        rows = [dict(row) for row in c.fetchall()]

        highlight_pattern = _get_highlight_pattern(query)
        for row in rows:
            title = row["title"]
            intro = row["intro"] or ""
            row["title_highlight"] = highlight_pattern.sub(
                lambda m: f"<mark>{m.group(0)}</mark>", title
            )
            row["intro_highlight"] = highlight_pattern.sub(
                lambda m: f"<mark>{m.group(0)}</mark>", intro
            )
        return rows


def db_search_count(query):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT COUNT(*) FROM news 
            WHERE instr(lower(title), lower(?)) OR instr(lower(intro), lower(?)) OR instr(lower(source), lower(?))
        """,
            (query, query, query),
        )
        count = c.fetchone()[0]
    return count


def db_get_news(limit=10, offset=0, source=None, search=None):
    with get_db() as conn:
        c = conn.cursor()
        query = """SELECT n.title, n.url, n.source, n.publish_time, n.publish_ts, n.intro, n.dedup_group,
               COALESCE((SELECT COUNT(*) FROM news n2 WHERE n2.dedup_group = n.dedup_group AND n2.dedup_group > 0), 1) AS dedup_count
               FROM news n"""
        params = []
        conditions = []
        if source:
            conditions.append("n.source = ?")
            params.append(source)
        if search:
            conditions.append("(instr(lower(n.title), lower(?)) OR instr(lower(n.intro), lower(?)))")
            params.extend([search, search])
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY COALESCE(NULLIF(publish_ts, 0), CAST(strftime('%s', created_at) AS INTEGER)) DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        c.execute(query, params)
        rows = [dict(row) for row in c.fetchall()]

        if search:
            highlight_pattern = _get_highlight_pattern(search)
            for row in rows:
                title = row["title"]
                intro = row["intro"] or ""
                row["title_highlight"] = highlight_pattern.sub(
                    lambda m: f"<mark>{m.group(0)}</mark>", title
                )
                row["intro_highlight"] = highlight_pattern.sub(
                    lambda m: f"<mark>{m.group(0)}</mark>", intro
                )
    return rows


def db_count(source=None, search=None):
    with get_db() as conn:
        c = conn.cursor()
        query = "SELECT COUNT(*) FROM news WHERE 1=1"
        params = []
        if source:
            query += " AND source = ?"
            params.append(source)
        if search:
            query += " AND (instr(lower(title), lower(?)) OR instr(lower(intro), lower(?)))"
            params.extend([search, search])
        c.execute(query, params)
        count = c.fetchone()[0]
    return count


def db_source_stats():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT source, COUNT(*) as count FROM news GROUP BY source ORDER BY count DESC")
        rows = c.fetchall()
    return {row["source"]: row["count"] for row in rows}


def db_get_all_for_export(start_date=None, end_date=None):
    with get_db() as conn:
        c = conn.cursor()
        query = "SELECT title, url, source, publish_time, publish_ts, intro FROM news WHERE 1=1"
        params = []
        if start_date:
            query += " AND publish_time >= ?"
            params.append(start_date)
        if end_date:
            query += " AND publish_time <= ?"
            params.append(end_date + " 23:59:59")
        query += " ORDER BY COALESCE(NULLIF(publish_ts, 0), CAST(strftime('%s', created_at) AS INTEGER)) DESC, id DESC"
        c.execute(query, params)
        rows = [dict(row) for row in c.fetchall()]
    return rows


def db_stream_news(start_date=None, end_date=None):
    """Generator that yields news rows one at a time for memory-efficient streaming exports"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        c = conn.cursor()
        query = "SELECT title, url, source, publish_time, publish_ts, intro FROM news WHERE 1=1"
        params = []
        if start_date:
            query += " AND publish_time >= ?"
            params.append(start_date)
        if end_date:
            query += " AND publish_time <= ?"
            params.append(end_date + " 23:59:59")
        query += " ORDER BY COALESCE(NULLIF(publish_ts, 0), CAST(strftime('%s', created_at) AS INTEGER)) DESC, id DESC"
        c.execute(query, params)
        for row in c:
            yield dict(row)
    finally:
        conn.close()


def db_backfill_publish_ts():
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) FROM news WHERE publish_ts = 0 AND publish_time IS NOT NULL"
        )
        count = c.fetchone()[0]
        if count == 0:
            return
        logger.info(f"回填 publish_ts: {count} 条记录")
        c.execute(
            "SELECT id, publish_time FROM news WHERE publish_ts = 0 AND publish_time IS NOT NULL"
        )
        rows = c.fetchall()
        for row in rows:
            ts = ts_from_bj_str(row["publish_time"])
            if ts > 0:
                c.execute(
                    "UPDATE news SET publish_ts = ? WHERE id = ?", (ts, row["id"])
                )
        conn.commit()
        logger.info(f"回填完成")


def db_backfill_dedup_fields():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM news WHERE title_full_hash IS NULL")
        count = c.fetchone()[0]
        if count == 0:
            return
        logger.info(f"回填去重字段: {count} 条记录")
        c.execute("SELECT id, title, url FROM news WHERE title_full_hash IS NULL")
        rows = c.fetchall()
        c.execute("SELECT MAX(dedup_group) FROM news")
        max_group = c.fetchone()[0] or 0
        for row in rows:
            title_full_hash = compute_title_full_hash(row["title"])
            url_hash = compute_url_hash(row["url"] or "")
            simhash_val = compute_simhash(row["title"])
            simhash_hex = f"{simhash_val:016x}"
            dedup_group = 0
            c.execute(
                "SELECT simhash, dedup_group FROM news WHERE simhash IS NOT NULL AND simhash != '' AND dedup_group > 0 AND publish_ts > ?",
                (int(time.time()) - 7 * 86400,),
            )
            existing = c.fetchall()
            for ex in existing:
                ex_simhash = (
                    int(ex["simhash"], 16)
                    if isinstance(ex["simhash"], str)
                    else ex["simhash"]
                )
                if hamming_distance(simhash_val, ex_simhash) <= 10:
                    dedup_group = ex["dedup_group"]
                    break
            if dedup_group == 0:
                max_group += 1
                dedup_group = max_group
            c.execute(
                "UPDATE news SET title_full_hash = ?, url_hash = ?, simhash = ?, dedup_group = ? WHERE id = ?",
                (title_full_hash, url_hash, simhash_hex, dedup_group, row["id"]),
            )
        conn.commit()
        logger.info(f"去重字段回填完成")


def db_cleanup_if_needed():
    if not os.path.exists(DB_PATH):
        return
    size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    if size_mb < MAX_DB_SIZE_MB:
        return
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM news")
        total = c.fetchone()[0]
        to_delete = int(total * 0.2)
        if to_delete > 0:
            c.execute(
                "SELECT id FROM news ORDER BY created_at ASC LIMIT ?", (to_delete,)
            )
            ids = [row[0] for row in c.fetchall()]
            c.execute(
                "DELETE FROM news WHERE id IN ({})".format(",".join("?" * len(ids))),
                ids,
            )
            conn.commit()
            logger.info(f"数据库清理: 删除 {len(ids)} 条最旧数据")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("VACUUM")
    conn.close()


source_last_ts: dict[str, int] = {
    "新浪财经": 0,
    "财联社": 0,
    "同花顺": 0,
    "东方财富": 0,
    "GDELT": 0,
    "雅虎财经": 0,
    "Google News": 0,
    "21经济网": 0,
    "华尔街见闻": 0,
    "雪球": 0,
    "金十数据": 0,
    "格隆汇": 0,
    "法布财经": 0,
}

SOURCE_COLORS = {
    "新浪财经": "#D94A4A",  # 红 ~0°
    "财联社": "#D94A7A",  # 玫红 ~340°
    "同花顺": "#E08A3A",  # 橙 ~30°
    "东方财富": "#E86A2A",  # 橙红 ~15°
    "GDELT": "#4A8A5A",  # 绿 ~140°
    "雅虎财经": "#8A5AC0",  # 紫 ~270°
    "Google News": "#4A8AD9",  # 蓝 ~210°
    "21经济网": "#3AA87A",  # 翠绿 ~160°
    "华尔街见闻": "#5A6ABF",  # 靛蓝 ~230°
    "雪球": "#4AA0D9",  # 天蓝 ~195°
    "金十数据": "#E07A4A",  # 暖橙 ~20°
    "格隆汇": "#3A5A8A",  # 深蓝 ~220°
    "法布财经": "#4AC0A0",  # 青绿 ~170°
}

FINANCE_NEWS_SOURCES = [
    {
        "name": "新浪财经",
        "url": "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=15",
        "headers": {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn/",
            "Accept": "application/json",
        },
    },
    {
        "name": "财联社",
        "url": "https://www.cls.cn/nodeapi/updateTelegraphList?rn=20&last_time=",
        "headers": {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.cls.cn/",
            "Accept": "application/json",
        },
    },
    {
        "name": "同花顺",
        "url": "https://news.10jqka.com.cn/tapp/news/push/stock",
        "headers": {
            "User-Agent": "Mozilla/5.0",
            "Referer": "http://news.10jqka.com.cn/",
            "Accept": "application/json",
        },
        "params": {"page": 1, "tag": "", "type": "all"},
    },
    {
        "name": "东方财富",
        "url": "https://np-listapi.eastmoney.com/comm/web/getFastNewsList",
        "headers": {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://kuaixun.eastmoney.com/",
            "Accept": "application/json",
        },
        "params": {
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "sortEnd": "",
            "pageSize": 20,
        },
    },
    {
        "name": "GDELT",
        "url": "https://api.gdeltproject.org/api/v2/doc/doc",
        "headers": {"User-Agent": "Mozilla/5.0"},
        "params": {
            "query": "finance economy stock market",
            "mode": "artlist",
            "format": "json",
            "maxrecords": 50,
        },
    },
    {
        "name": "雅虎财经",
        "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY,AAPL,MSFT&region=US&lang=en-US",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        },
    },
    {
        "name": "Google News",
        "url": "https://news.google.com/rss?topic=b&hl=en-US&gl=US&ceid=US:en",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        },
    },
    {
        "name": "21经济网",
        "url": "https://api.21jingji.com/timestream/getListweb?page=1",
        "headers": {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.21jingji.com/",
            "Accept": "application/json",
        },
    },
    {
        "name": "华尔街见闻",
        "url": "https://api-one.wallstcn.com/apiv1/content/information-flow?channel=global-channel&accept=article&limit=30",
        "headers": {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://wallstreetcn.com/",
            "Accept": "application/json",
        },
    },
    {
        "name": "雪球",
        "url": "https://xueqiu.com/u/5124430882",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://xueqiu.com/",
            "Accept": "text/html",
        },
    },
    {
        "name": "金十数据",
        "url": "https://www.jin10.com/flash_newest.js",
        "headers": {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.jin10.com/",
            "Accept": "*/*",
        },
    },
    {
        "name": "格隆汇",
        "url": "https://www.gelonghui.com/news/",
        "headers": {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.gelonghui.com/",
            "Accept": "text/html",
        },
    },
    {
        "name": "法布财经",
        "url": "https://www.fastbull.com/cn/express-news",
        "headers": {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.fastbull.com/",
            "Accept": "text/html",
        },
    },
]


# 不同源的特殊配置
SOURCE_TIMEOUTS = {
    "Google News": 15.0,
    "GDELT": 15.0,
    "雪球": 12.0,
    "金十数据": 10.0,
    "格隆汇": 12.0,
    "法布财经": 12.0,
}

SOURCE_SKIP_REQ_TRACE = {"GDELT", "Google News", "21经济网"}


async def fetch_news_from_source(source: dict) -> list:
    news_list = []
    source_name = source["name"]
    last_ts = source_last_ts.get(source_name, 0)
    timeout = SOURCE_TIMEOUTS.get(source_name, 8.0)

    try:
        # 按来源速率限制（GDELT 免费 API 限制严格）
        min_interval = SOURCE_RATE_LIMITS.get(source_name, 0)
        if min_interval > 0:
            elapsed = time.time() - _last_source_req.get(source_name, 0)
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)

        ssl_ctx = gdelt_ssl_context if source_name == "GDELT" else True

        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            verify=ssl_ctx,
        ) as client:
            kwargs = {"url": source["url"], "headers": source["headers"]}
            method = source.get("method", "GET")
            if "params" in source and source_name not in SOURCE_SKIP_REQ_TRACE:
                params_dict = dict(source["params"])
                if method == "GET":
                    kwargs["params"] = params_dict
                    kwargs["params"]["req_trace"] = str(int(time.time() * 1000))
                else:
                    kwargs["data"] = params_dict
            elif "params" in source and source_name in SOURCE_SKIP_REQ_TRACE:
                kwargs["params"] = dict(source["params"])

            if method == "POST":
                response = await client.post(**kwargs)
            else:
                response = await client.get(**kwargs)

            # 记录请求时间（用于速率限制）
            if min_interval > 0:
                _last_source_req[source_name] = time.time()

            if response.status_code == 429:
                logger.warning(f"{source_name} 触发速率限制 (429)，等待重试")
                retry_after = response.headers.get("Retry-After")
                wait = int(retry_after) if retry_after and retry_after.isdigit() else 30
                await asyncio.sleep(wait)
                # 重试一次
                if method == "POST":
                    response = await client.post(**kwargs)
                else:
                    response = await client.get(**kwargs)
                if min_interval > 0:
                    _last_source_req[source_name] = time.time()

            if response.status_code != 200:
                logger.warning(f"获取{source_name}失败：HTTP {response.status_code}")
                return news_list

            # Google News 返回 RSS XML
            if source_name == "Google News":
                soup = BeautifulSoup(response.text, "xml")
                items = soup.find_all("item")
                for item in items:
                    title_tag = item.find("title")
                    source_tag = item.find("source")
                    pub_date_tag = item.find("pubDate")
                    link_tag = item.find("link")
                    desc_tag = item.find("description")

                    full_title = title_tag.text if title_tag else ""
                    parts = full_title.rsplit(" - ", 1)
                    if len(parts) == 2:
                        clean_title, source_from_title = parts
                    else:
                        clean_title = full_title
                        source_from_title = ""

                    source_from_tag = (
                        source_tag.text if source_tag else source_from_title
                    )

                    pub_date = pub_date_tag.text if pub_date_tag else ""
                    ts = 0
                    pt = now_bj().strftime("%Y-%m-%d %H:%M:%S")
                    try:
                        pub_date_clean = pub_date.strip()
                        if pub_date_clean.endswith(" GMT"):
                            pub_date_clean = pub_date_clean[:-4] + " +0000"
                        dt = datetime.strptime(
                            pub_date_clean, "%a, %d %b %Y %H:%M:%S %z"
                        )
                        ts = int(dt.timestamp())
                        pt = bj_str_from_ts(ts)
                    except (ValueError, TypeError):
                        try:
                            pub_date_clean2 = (
                                pub_date.strip().replace("GMT", "+0000").strip()
                            )
                            dt = datetime.strptime(
                                pub_date_clean2, "%a, %d %b %Y %H:%M:%S %z"
                            )
                            ts = int(dt.timestamp())
                            pt = bj_str_from_ts(ts)
                        except (ValueError, TypeError):
                            logger.warning(f"Google News时间解析失败: {pub_date}")

                    if ts <= last_ts:
                        continue

                    link = link_tag.text if link_tag else "#"

                    desc_html = desc_tag.text if desc_tag else ""
                    intro = ""
                    if desc_html:
                        desc_soup = BeautifulSoup(desc_html, "lxml")
                        first_link = desc_soup.find("a")
                        if first_link and first_link.parent.name == "li":
                            intro = first_link.parent.get_text(strip=True)[:150]
                        else:
                            intro = desc_soup.get_text(strip=True)[:150]

                    news_list.append(
                        {
                            "title": clean_title.strip() or "无标题",
                            "url": link,
                            "source": source_name,
                            "publish_time": pt,
                            "publish_ts": ts,
                            "intro": f"[{source_from_tag}] {intro}"
                            if source_from_tag
                            else intro,
                        }
                    )

            # 21经济网 - 快讯API (JSON)
            elif source_name == "21经济网":
                data = response.json()
                items = data.get("list", [])
                for item in items:
                    title = (item.get("title") or "").strip()
                    if not title:
                        continue
                    time_str = item.get("inputtime", "") or ""
                    # 21经济网 inputtime 格式为 "2026-05-17 08:58"（无秒数），需要补 :00 才能解析
                    if time_str and len(time_str) == 16:
                        time_str += ":00"
                    ts = ts_from_bj_str(time_str)
                    if ts <= last_ts:
                        continue
                    pt = (
                        bj_str_from_ts(ts)
                        if ts
                        else now_bj().strftime("%Y-%m-%d %H:%M:%S")
                    )
                    url = item.get("url", "") or "#"
                    content_raw = (item.get("content") or "").strip()
                    intro = re.sub(r"\s+", " ", content_raw).strip()[:150]
                    news_list.append(
                        {
                            "title": title[:80],
                            "url": url,
                            "source": source_name,
                            "publish_time": pt,
                            "publish_ts": ts,
                            "intro": intro,
                        }
                    )

            # GDELT API
            elif source_name == "GDELT":
                data = response.json()
                articles = data.get("articles", [])
                for a in articles:
                    seendate = a.get("seendate", "")
                    ts = 0
                    try:
                        if seendate:
                            dt = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ")
                            dt = dt.replace(tzinfo=timezone.utc)
                            ts = int(dt.timestamp())
                            pt = bj_str_from_ts(ts)
                    except (ValueError, TypeError):
                        pt = now_bj().strftime("%Y-%m-%d %H:%M:%S")
                        ts = 0
                    if ts <= last_ts:
                        continue
                    title = (a.get("title") or "无标题").strip()
                    url = a.get("url", "#")
                    source_info = a.get("sourcecountry", "")
                    news_list.append(
                        {
                            "title": title[:80] or "无标题",
                            "url": url,
                            "source": source_name,
                            "publish_time": pt,
                            "publish_ts": ts,
                            "intro": f"[{source_info}]" if source_info else "",
                        }
                    )

            # 华尔街见闻 - JSON API
            elif source_name == "华尔街见闻":
                data = response.json()
                articles = data.get("data", {}).get("items", [])
                for a in articles:
                    if a.get("resource_type") in ("theme", "ad"):
                        continue
                    resource = a.get("resource", {})
                    title = (
                        resource.get("title", "") or resource.get("content_short", "")
                    ).strip()
                    if not title:
                        continue
                    display_time = resource.get("display_time", 0)
                    ts = int(display_time) if display_time else 0
                    if ts <= last_ts:
                        continue
                    pt = (
                        bj_str_from_ts(ts)
                        if ts
                        else now_bj().strftime("%Y-%m-%d %H:%M:%S")
                    )
                    url = resource.get("uri", "")
                    if url and not url.startswith("http"):
                        url = f"https://wallstreetcn.com{url}"
                    news_list.append(
                        {
                            "title": title[:80],
                            "url": url or "#",
                            "source": source_name,
                            "publish_time": pt,
                            "publish_ts": ts,
                            "intro": (resource.get("content_short", "") or "")[:150],
                        }
                    )

            # 雪球 - 7x24快讯 HTML抓取
            elif source_name == "雪球":
                soup = BeautifulSoup(response.text, "html.parser")
                articles = soup.select(
                    ".timeline__item, .status-item, [class*='timeline'] li, [class*='status'] li"
                )
                if not articles:
                    articles = soup.find_all("li")

                for article in articles:
                    content_elem = article.select_one(".content, [class*='content'], p")
                    time_elem = article.select_one(
                        ".time, [class*='time'], [class*='date']"
                    )
                    title_elem = article.select_one(".title, [class*='title']")

                    if not content_elem:
                        continue

                    content = content_elem.get_text(strip=True)[:80]
                    if len(content) < 4:
                        continue

                    ts = 0
                    pt = now_bj().strftime("%Y-%m-%d %H:%M:%S")
                    if time_elem:
                        time_text = time_elem.get_text(strip=True)
                        if time_text and re.match(r"\d{4}-\d{2}-\d{2}", time_text):
                            try:
                                dt = datetime.strptime(
                                    time_text[:19], "%Y-%m-%d %H:%M:%S"
                                )
                                dt = dt.replace(tzinfo=timezone.utc)
                                ts = int(dt.timestamp())
                                pt = bj_str_from_ts(ts)
                            except ValueError:
                                pass

                    if ts <= last_ts:
                        continue

                    link = "#"
                    a_tag = article.find("a", href=True)
                    if a_tag:
                        link = a_tag["href"]
                        if not link.startswith("http"):
                            link = f"https://xueqiu.com{link}"

                    title = ""
                    if title_elem:
                        title = title_elem.get_text(strip=True)
                    display_title = title if title else content[:60]

                    news_list.append(
                        {
                            "title": display_title[:80],
                            "url": link,
                            "source": source_name,
                            "publish_time": pt,
                            "publish_ts": ts,
                            "intro": content[:150],
                        }
                    )

            # 金十数据 - JavaScript变量响应
            elif source_name == "金十数据":
                text = response.text
                text = re.sub(r"^var\s+newest\s*=\s*", "", text)
                text = text.rstrip(";").strip()
                if text:
                    data = json.loads(text)
                    for item in data:
                        # 跳过广告内容（基于 type 字段）
                        if str(item.get("type", "")).lower() in ("ad", "advert", "promotion"):
                            continue
                        if item.get("vip"):
                            continue
                        if 5 in (item.get("channel") or []):
                            continue
                        data_content = item.get("data", {})
                        # 内容层过滤：标题/内容包含营销关键词则跳过
                        title_raw = (
                            data_content.get("title", "")
                            or data_content.get("content", "")
                        ).strip()
                        if any(kw in title_raw for kw in ("VIP会员", "立减", "开通>>", "折扣")):
                            continue
                        title_raw = re.sub(r"<[^>]+>", "", title_raw)
                        m = re.match(r"^【([^】]*)】(.*)$", title_raw)
                        if m:
                            title = m.group(1).strip()
                            desc = m.group(2).strip()
                        else:
                            title = title_raw
                            desc = ""
                        if not title:
                            continue
                        time_str = item.get("time", "")
                        ts = ts_from_bj_str(time_str)
                        if ts <= last_ts:
                            continue
                        pt = (
                            bj_str_from_ts(ts)
                            if ts
                            else now_bj().strftime("%Y-%m-%d %H:%M:%S")
                        )
                        news_list.append(
                            {
                                "title": title[:80],
                                "url": f"https://flash.jin10.com/detail/{item.get('id', '')}",
                                "source": source_name,
                                "publish_time": pt,
                                "publish_ts": ts,
                                "intro": desc[:150] if desc else "",
                            }
                        )

            # 格隆汇 - HTML抓取
            elif source_name == "格隆汇":
                soup = BeautifulSoup(response.text, "html.parser")
                articles = soup.select(".article-content")
                for article in articles:
                    link_elem = article.select_one(".detail-right > a")
                    if not link_elem:
                        continue
                    url = link_elem.get("href", "")
                    if url and not url.startswith("http"):
                        url = f"https://www.gelonghui.com{url}"
                    title_elem = link_elem.select_one("h2")
                    title = title_elem.get_text(strip=True) if title_elem else ""
                    if not title:
                        continue
                    info_elem = article.select_one(".time > span:nth-child(1)")
                    info = info_elem.get_text(strip=True) if info_elem else ""
                    time_elem = article.select_one(".time > span:nth-child(3)")
                    time_str = time_elem.get_text(strip=True) if time_elem else ""
                    ts = parse_relative_time(time_str)
                    if ts <= last_ts:
                        continue
                    pt = (
                        bj_str_from_ts(ts)
                        if ts
                        else now_bj().strftime("%Y-%m-%d %H:%M:%S")
                    )
                    news_list.append(
                        {
                            "title": title[:80],
                            "url": url or "#",
                            "source": source_name,
                            "publish_time": pt,
                            "publish_ts": ts,
                            "intro": info[:150] if info else "",
                        }
                    )

            # 法布财经 - HTML抓取
            elif source_name == "法布财经":
                soup = BeautifulSoup(response.text, "html.parser")
                articles = soup.select(".news-list")
                for article in articles:
                    title_elem = article.select_one(".title_name")
                    if not title_elem:
                        continue
                    title_raw = title_elem.get_text(strip=True)
                    m = re.search(r"【([^】]+)】", title_raw)
                    if m:
                        title = m.group(1).strip()
                    else:
                        title = title_raw
                    if len(title) < 4:
                        continue
                    date_attr = article.get("data-date", "")
                    ts = int(date_attr) // 1000 if date_attr.isdigit() else 0
                    if ts <= last_ts:
                        continue
                    pt = (
                        bj_str_from_ts(ts)
                        if ts
                        else now_bj().strftime("%Y-%m-%d %H:%M:%S")
                    )
                    news_list.append(
                        {
                            "title": title[:80],
                            "url": "#",
                            "source": source_name,
                            "publish_time": pt,
                            "publish_ts": ts,
                            "intro": "",
                        }
                    )

            # 雅虎财经 - RSS XML
            elif source_name == "雅虎财经":
                soup = BeautifulSoup(response.text, "xml")
                items = soup.find_all("item")
                for item in items:
                    title_tag = item.find("title")
                    link_tag = item.find("link")
                    pub_date_tag = item.find("pubDate")
                    desc_tag = item.find("description")

                    title = (title_tag.text if title_tag else "无标题").strip()
                    link = link_tag.text if link_tag else "#"

                    ts = 0
                    pt = now_bj().strftime("%Y-%m-%d %H:%M:%S")
                    pub_date = pub_date_tag.text if pub_date_tag else ""
                    try:
                        if pub_date:
                            pub_clean = pub_date.strip()
                            if pub_clean.endswith(" GMT"):
                                pub_clean = pub_clean[:-4] + " +0000"
                            dt = datetime.strptime(
                                pub_clean, "%a, %d %b %Y %H:%M:%S %z"
                            )
                            ts = int(dt.timestamp())
                            pt = bj_str_from_ts(ts)
                    except (ValueError, TypeError):
                        pass

                    if ts <= last_ts:
                        continue

                    intro = ""
                    if desc_tag and desc_tag.text:
                        desc_soup = BeautifulSoup(desc_tag.text, "lxml")
                        intro = desc_soup.get_text(strip=True)[:150]

                    news_list.append(
                        {
                            "title": title,
                            "url": link,
                            "source": source_name,
                            "publish_time": pt,
                            "publish_ts": ts,
                            "intro": intro,
                        }
                    )

            else:
                # JSON 源解析
                data = response.json()

                if source_name == "新浪财经":
                    for a in data.get("result", {}).get("data", []):
                        ctime = a.get("ctime", "")
                        ts = int(ctime) if ctime and str(ctime).isdigit() else 0
                        if ts <= last_ts:
                            continue
                        pt = bj_str_from_ts(ts)
                        news_list.append(
                            {
                                "title": (a.get("title") or "无标题").strip(),
                                "url": a.get("url", "#"),
                                "source": source_name,
                                "publish_time": pt,
                                "publish_ts": ts,
                                "intro": (a.get("intro", "") or "")[:150],
                            }
                        )

                elif source_name == "财联社":
                    for a in data.get("data", {}).get("roll_data", []):
                        ctime = a.get("ctime", "")
                        ts = int(ctime) if ctime and str(ctime).isdigit() else 0
                        if ts <= last_ts:
                            continue
                        pt = bj_str_from_ts(ts)
                        title = (
                            a.get("title") or a.get("brief", "") or "无标题"
                        ).strip()[:50]
                        news_list.append(
                            {
                                "title": title or "无标题",
                                "url": f"https://www.cls.cn/detail/{a.get('id', '')}"
                                if a.get("id")
                                else (a.get("shareurl", "#")),
                                "source": source_name,
                                "publish_time": pt,
                                "publish_ts": ts,
                                "intro": (
                                    a.get("brief", "") or a.get("content", "") or ""
                                )[:150],
                            }
                        )

                elif source_name == "同花顺":
                    for a in data.get("data", {}).get("list", []):
                        ctime = a.get("ctime", "")
                        ts = int(ctime) if ctime and str(ctime).isdigit() else 0
                        if ts <= last_ts:
                            continue
                        pt = bj_str_from_ts(ts)
                        share_url = a.get("shareUrl", "")
                        url = "#"
                        if share_url and "/share/" in share_url:
                            m = re.search(r"/share/(\d+)/?", share_url)
                            if m:
                                aid = m.group(1)
                                date_str = bj_str_from_ts(ts)[:10].replace("-", "")
                                url = f"https://news.10jqka.com.cn/{date_str}/c{aid}.shtml"
                            else:
                                url = share_url
                        elif share_url:
                            url = share_url
                        news_list.append(
                            {
                                "title": (a.get("title") or "无标题").strip(),
                                "url": url,
                                "source": source_name,
                                "publish_time": pt,
                                "publish_ts": ts,
                                "intro": (
                                    a.get("digest", "") or a.get("short", "") or ""
                                )[:150],
                            }
                        )

                elif source_name == "东方财富":
                    for a in data.get("data", {}).get("fastNewsList", []):
                        st = a.get("showTime", "")
                        ts = ts_from_bj_str(st)
                        if ts <= last_ts:
                            continue
                        pt = st[:19] if st else now_bj().strftime("%Y-%m-%d %H:%M:%S")
                        code = a.get("code", "")
                        news_list.append(
                            {
                                "title": (a.get("title") or "无标题").strip(),
                                "url": f"https://finance.eastmoney.com/a/{code}.html"
                                if code
                                else "#",
                                "source": source_name,
                                "publish_time": pt,
                                "publish_ts": ts,
                                "intro": (a.get("summary", "") or "")[:150],
                            }
                        )

    except httpx.ConnectTimeout:
        logger.warning(f"获取{source_name}失败：连接超时")
    except httpx.ConnectError as e:
        logger.warning(f"获取{source_name}失败：连接错误 - {str(e)[:60]}")
    except Exception as e:
        logger.warning(f"获取{source_name}失败：{str(e)}")

    if news_list:
        timestamps = [n["publish_ts"] for n in news_list if n.get("publish_ts", 0) > 0]
        if timestamps:
            source_last_ts[source_name] = max(timestamps)
    return news_list


async def fetch_new_news() -> tuple:
    tasks = [fetch_news_from_source(s) for s in FINANCE_NEWS_SOURCES]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_news, source_stats = [], {}
    for s, r in zip(FINANCE_NEWS_SOURCES, results):
        name = s["name"]
        if isinstance(r, list):
            all_news.extend(r)
            source_stats[name] = len(r)
        else:
            source_stats[name] = 0
            logger.warning(f"抓取{name}异常: {r}")
    all_news.sort(key=lambda x: x.get("publish_time", ""), reverse=True)
    return all_news, source_stats


FETCH_INTERVAL = 30
last_fetch_result: dict = {
    "source_stats": {},
    "new_hashes": [],
    "new_count": 0,
    "update_time": "",
}


active_connections: set[WebSocket] = set()

_trending_cache: dict = {"data": [], "updated_at": "", "expires_at": 0}
TRENDING_CACHE_TTL = 300

# --- Timeline: extract upcoming events from real-time news ---
_TIMELINE_CATEGORIES_CONFIG = {
    "国际热点": ["美联储", "G20", "欧盟", "日本央行", "中东", "WTO", "IMF", "世卫", "联合国", "白宫", "欧洲央行",
                "特朗普", "拜登", "普京", "全球", "国际", "海外", "对华", "关税", "制裁", "以色列", "伊朗",
                "俄罗斯", "乌克兰", "美国", "美股", "纳指", "标普", "道指", "欧股", "日经", "亚太",
                "地缘", "OPEC", "原油", "黄金", "汇率", "美元指数", "非农", "CPI", "PPI", "贸易战"],
    "国内热点": ["国务院", "央行", "证监会", "银保监会", "财政部", "发改委", "两会", "人大", "政协", "工信部",
                "商务部", "人社部", "住建部", "总理", "政治局", "国家统计局", "GDP", "PMI",
                "降准", "降息", "LPR", "MLF", "逆回购", "货币政策", "财政政策", "专项债", "特别国债",
                "中央经济", "乡村振兴", "扩大内需", "消费", "投资", "基建", "房地产", "楼市",
                "限购", "认房不认贷", "首付", "利率", "公积金贷款"],
    "社会热点": ["高考", "医保", "养老", "社保", "公积金", "高温", "暴雨", "台风", "疫情", "放假",
                "假期", "春运", "出行", "油价", "环保", "个人所得税", "养老金", "落户", "限购", "招聘",
                "裁员", "工资", "最低工资", "物价", "消费品", "食品安全"],
    "行业热点": ["AI", "大模型", "芯片", "半导体", "新能源", "光伏", "电池", "储能", "氢能",
                "低空", "算力", "云计算", "人工智能", "机器人", "无人驾驶", "智能驾驶", "5G", "6G",
                "生物医药", "创新药", "CXO", "医疗器械", "风电", "核电", "碳中和", "固态电池",
                "量子", "数据要素", "飞行汽车", "自动驾驶", "AIGC", "大语言模型", "Sora",
                "HBM", "先进封装", "光刻", "EDA", "信创", "数字经济", "Web3", "区块链",
                "智能座舱", "一体化压铸", "磷酸铁锂", "钠离子", "钙钛矿"],
    "公司热点": [".SH)", ".SZ", ".HK)", ".O)", "腾讯", "阿里", "京东", "美团", "拼多多", "华为", "小米",
                "比亚迪", "宁德时代", "字节", "百度", "网易", "蔚来", "小鹏", "理想", "特斯拉", "苹果",
                "微软", "茅台", "工商银行", "中国平安", "招商银行", "SpaceX", "台积电", "三星",
                "IPO", "上市", "并购", "重组", "融资", "收购", "定增", "借壳", "分拆",
                "港股", "科创板", "创业板", "北交所", "注册制"],
    "个股公告": ["公告", "业绩预告", "业绩快报", "财报", "季报", "年报", "分红", "送转",
                "增发", "配股", "回购", "减持", "增持", "质押", "解禁",
                "中标", "股权激励", "停牌", "复牌", "ST", "*ST", "退市",
                "提案", "预案", "申请书", "受理", "股东会", "股东大会", "董事会",
                "分配方案", "除权", "除息", "股权登记", "缴款", "配股"],
}
_TIMELINE_DATA_CACHE = {"data": [], "updated_at": 0}
_UPCOMING_KEYWORDS = [
    "即将", "将于", "拟", "计划", "预计", "预期", "将在", "将要", "下周", "下月",
    "下季度", "即将推出", "即将发布", "即将召开", "即将举行", "即将公布",
    "正在推进", "筹备", "酝酿", "在即", "有望", "启动", "目标", "意向",
    "申请", "受理", "审核", "过会", "注册", "待", "静待", "倒计时",
    "临近", "来临", "进入", "冲刺", "备战", "率", "预",
    "新股申购", "中签", "缴款", "上市", "挂牌",
    "股权登记", "除权", "除息", "分红", "送转", "派息",
    "股东大会", "股东会", "临时会议", "表决",
    "入围", "中标", "签约", "框架协议",
]


def _is_upcoming_event(title: str) -> bool:
    for kw in _UPCOMING_KEYWORDS:
        if kw in title:
            return True
    return False


def _classify_timeline_category(title: str) -> str:
    for cat, keywords in _TIMELINE_CATEGORIES_CONFIG.items():
        for kw in keywords:
            if kw in title:
                return cat
    return "社会热点"


def _extract_timeline_from_news(all_news: list) -> list:
    events = []
    seen_titles = set()
    today = now_bj().date()
    day_offsets = list(range(0, 31))
    idx = 0
    for news in all_news:
        title = news.get("title", "").strip()
        intro = news.get("intro", "").strip()
        if not title or len(title) < 4:
            continue
        if not _is_upcoming_event(title):
            continue
        category = _classify_timeline_category(title)
        desc = intro if intro else title
        key = title[:20]
        if key in seen_titles:
            continue
        seen_titles.add(key)
        offset = day_offsets[idx % len(day_offsets)]
        event_date = today + timedelta(days=offset)
        events.append({
            "id": idx + 1,
            "date": event_date.strftime("%Y-%m-%d"),
            "title": title[:80],
            "category": category,
            "importance": 2,
            "description": desc[:200],
        })
        idx += 1
    events.sort(key=lambda x: (x["date"], x["id"]))
    return events[:60]


# --- Option 4: Dedicated scrapers for announcement/calendar data ---
_IPO_DATE_TYPE_LABEL = {
    "申购": "新股申购日",
    "中签率": "新股中签率公布日",
    "中签号": "新股中签号公布日",
    "缴款日": "新股缴款日",
    "上市": "新股上市日",
}

async def fetch_ipo_calendar() -> list:
    events = []
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://data.eastmoney.com/",
                },
                params={
                    "reportName": "RPT_IPO_CALENDAR",
                    "columns": "SECUCODE,TRADE_DATE,DATE_TYPE,SECURITY_CODE,SECURITY_NAME_ABBR",
                    "pageNumber": 1,
                    "pageSize": 100,
                    "sortTypes": -1,
                    "sortColumns": "TRADE_DATE",
                    "source": "WEB",
                    "client": "WEB",
                },
            )
            if r.status_code != 200:
                return events
            body = r.json()
            data_list = (body.get("result") or {}).get("data") or []
            today = now_bj().date()
            for item in data_list:
                date_str = (item.get("TRADE_DATE") or "")[:10]
                if not date_str:
                    continue
                event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                if event_date < today:
                    continue
                if event_date > today + timedelta(days=31):
                    continue
                name = item.get("SECURITY_NAME_ABBR", "")
                dtype = item.get("DATE_TYPE", "")
                label = _IPO_DATE_TYPE_LABEL.get(dtype, dtype)
                title = f"{name} {label}"
                category = "个股公告"
                if "上市" in dtype:
                    category = "公司热点"
                events.append({
                    "date": date_str,
                    "title": title,
                    "category": category,
                    "importance": 3 if "上市" in dtype else 2,
                    "description": f"{name}（{item.get('SECURITY_CODE','')}）{label}，日期：{date_str}",
                    "source": "ipo_calendar",
                    "source_url": f"https://data.eastmoney.com/xg/xg/dq/{item.get('SECURITY_CODE','')}.html",
                })
            logger.info(f"新股日历爬取完成: {len(events)} 条")
    except Exception as e:
        logger.warning(f"新股日历爬取失败: {e}")
    return events


async def fetch_sina_announcements() -> list:
    events = []
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(
                "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2510&num=30",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://finance.sina.com.cn/",
                    "Accept": "application/json",
                },
            )
            if r.status_code != 200:
                return events
            body = r.json()
            raw_data = (body.get("result") or {}).get("data") or []
            if isinstance(raw_data, dict):
                raw_data = raw_data.get("data") or []
            items = raw_data if isinstance(raw_data, list) else []
            today = now_bj().date()
            for item in items:
                title = (item.get("title") or item.get("stitle") or "").strip()
                if not title or len(title) < 4:
                    continue
                if not _is_upcoming_event(title):
                    continue
                category = _classify_timeline_category(title)
                day_offsets = list(range(0, 31))
                idx = len(events)
                offset = day_offsets[idx % len(day_offsets)]
                event_date = today + timedelta(days=offset)
                events.append({
                    "date": event_date.strftime("%Y-%m-%d"),
                    "title": title[:80],
                    "category": category,
                    "importance": 2,
                    "description": title[:200],
                    "source": "sina_announcement",
                    "source_url": item.get("url", ""),
                })
            logger.info(f"新浪公告爬取完成: {len(events)} 条")
    except Exception as e:
        logger.warning(f"新浪公告爬取失败: {e}")
    return events


def _insert_timeline_events(events: list):
    if not events:
        return
    with get_db() as conn:
        c = conn.cursor()
        for ev in events:
            event_hash = hashlib.md5(
                f"{ev['date']}|{ev['title'][:40]}|{ev['category']}".encode()
            ).hexdigest()[:16]
            try:
                c.execute(
                    """INSERT OR IGNORE INTO timeline_events
                       (event_date, title, category, importance, description, source, source_url, event_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ev["date"],
                        ev["title"],
                        ev["category"],
                        ev.get("importance", 2),
                        ev.get("description", ""),
                        ev.get("source", "crawler"),
                        ev.get("source_url", ""),
                        event_hash,
                    ),
                )
            except Exception:
                pass
        conn.commit()


def _load_timeline_from_db() -> list:
    events = []
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                """SELECT event_date, title, category, importance, description, source, source_url
                   FROM timeline_events
                   WHERE event_date >= date('now','localtime')
                   ORDER BY event_date ASC, id ASC
                   LIMIT 80"""
            )
            for row in c.fetchall():
                events.append({
                    "id": len(events) + 1,
                    "date": row["event_date"],
                    "title": row["title"],
                    "category": row["category"],
                    "importance": row["importance"],
                    "description": row["description"],
                    "source": row["source"],
                    "source_url": row["source_url"],
                })
    except Exception as e:
        logger.warning(f"从DB加载时间线失败: {e}")
    return events


async def _build_timeline_cache():
    all_events = []

    ipo_events = await fetch_ipo_calendar()
    all_events.extend(ipo_events)

    sina_events = await fetch_sina_announcements()
    all_events.extend(sina_events)

    _insert_timeline_events(all_events)

    db_events = _load_timeline_from_db()
    _TIMELINE_DATA_CACHE["data"] = db_events
    _TIMELINE_DATA_CACHE["updated_at"] = time.time()
    if all_events:
        logger.info(f"时间线已重建: {len(_TIMELINE_DATA_CACHE['data'])} 条（IPO {len(ipo_events)} + 公告 {len(sina_events)}）")


async def _timeline_startup_build():
    await asyncio.sleep(3)
    try:
        await _build_timeline_cache()
    except Exception as e:
        logger.warning(f"启动时时间线构建失败: {e}")


async def _background_fetch_loop():
    while True:
        try:
            all_news, source_stats = await fetch_new_news()
            new_hashes, inserted = db_insert_news(all_news)
            last_fetch_result["source_stats"] = source_stats
            last_fetch_result["new_hashes"] = new_hashes
            last_fetch_result["new_count"] = inserted
            last_fetch_result["update_time"] = now_bj().strftime("%Y-%m-%d %H:%M:%S")
            if inserted > 0:
                logger.info(f"后台抓取完成: 新增 {inserted} 条")
                news_list = []
                for h in new_hashes:
                    with get_db() as conn:
                        c = conn.cursor()
                        c.execute(
                            "SELECT title, url, source, publish_time, publish_ts, intro FROM news WHERE title_hash = ?",
                            (h,),
                        )
                        row = c.fetchone()
                        if row:
                            news_list.append(dict(row))
                message = json.dumps({"type": "new_news", "data": news_list, "count": inserted})
                disconnected = set()
                for ws in active_connections:
                    try:
                        await ws.send_text(message)
                    except Exception:
                        disconnected.add(ws)
                active_connections.difference_update(disconnected)
            if inserted > 0 or True:
                new_events = _extract_timeline_from_news(all_news)
                existing = {e["title"][:20] for e in _TIMELINE_DATA_CACHE["data"]}
                merged = _TIMELINE_DATA_CACHE["data"][:]
                for ev in new_events:
                    if ev["title"][:20] not in existing:
                        merged.append(ev)
                        existing.add(ev["title"][:20])
                merged.sort(key=lambda x: (x["date"], x["id"]))
                _TIMELINE_DATA_CACHE["data"] = merged[:80]
                _TIMELINE_DATA_CACHE["updated_at"] = time.time()
                logger.info(f"时间线已更新: {len(_TIMELINE_DATA_CACHE['data'])} 条事件")
            _timeline_build_counter = getattr(_background_fetch_loop, '_build_counter', 0) + 1
            _background_fetch_loop._build_counter = _timeline_build_counter
            if _timeline_build_counter >= 10:
                _background_fetch_loop._build_counter = 0
                logger.info("开始重建时间线缓存（IPO日历+新浪公告）...")
                try:
                    ipo_events = await fetch_ipo_calendar()
                    sina_events = await fetch_sina_announcements()
                    crawled = ipo_events + sina_events
                    if crawled:
                        _insert_timeline_events(crawled)
                    db_events = _load_timeline_from_db()
                    news_titles = {e["title"][:20] for e in _TIMELINE_DATA_CACHE["data"]}
                    merged = _TIMELINE_DATA_CACHE["data"][:]
                    db_title_set = {e["title"][:20] for e in db_events}
                    for ev in db_events:
                        if ev["title"][:20] not in news_titles:
                            merged.append(ev)
                            news_titles.add(ev["title"][:20])
                    for i, ev in enumerate(merged):
                        ev["id"] = i + 1
                    merged.sort(key=lambda x: (x["date"], x["id"]))
                    _TIMELINE_DATA_CACHE["data"] = merged[:100]
                    _TIMELINE_DATA_CACHE["updated_at"] = time.time()
                    logger.info(f"时间线重建完成: 总计 {len(_TIMELINE_DATA_CACHE['data'])} 条（IPO {len(ipo_events)} + 公告 {len(sina_events)}）")
                except Exception as e:
                    logger.error(f"时间线重建失败: {e}")
        except Exception as e:
            logger.error(f"后台抓取异常: {e}")
        await asyncio.sleep(FETCH_INTERVAL)


@app.get("/api/poll")
async def poll_news(since_ts: int = Query(...)):
    deadline = time.time() + 15
    while time.time() < deadline:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                """SELECT n.title, n.url, n.source, n.publish_time, n.publish_ts, n.intro, n.dedup_group,
                   COALESCE((SELECT COUNT(*) FROM news n2 WHERE n2.dedup_group = n.dedup_group AND n2.dedup_group > 0), 1) AS dedup_count
                   FROM news n WHERE n.publish_ts > ? ORDER BY COALESCE(NULLIF(publish_ts, 0), CAST(strftime('%s', created_at) AS INTEGER)) DESC, id DESC""",
                (since_ts,),
            )
            rows = [dict(row) for row in c.fetchall()]
        if rows:
            return JSONResponse(
                status_code=200,
                content={"success": True, "data": rows, "total": len(rows)},
            )
        await asyncio.sleep(1)
    return JSONResponse(
        status_code=200,
        content={"success": True, "data": [], "total": 0},
    )


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse("static/favicon.png", media_type="image/png")


@app.get("/api/news")
async def get_news_api(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=5, le=50),
    source: str = Query(None),
    search: str = Query(None),
):
    try:
        total = db_count(source=source, search=search)
        offset = (page - 1) * page_size
        all_news = db_get_news(limit=page_size, offset=offset, source=source, search=search)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": all_news,
                "total": total,
                "page": page,
                "page_size": page_size,
                "new_hashes": last_fetch_result["new_hashes"],
                "new_count": last_fetch_result["new_count"],
                "source_stats": db_source_stats(),
                "update_time": last_fetch_result["update_time"]
                or now_bj().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
    except Exception as e:
        logger.error(f"获取新闻失败: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "获取新闻失败，请稍后重试",
                "data": [],
            },
        )


@app.get("/api/search")
async def search_news_api(
    query: str = Query(..., min_length=1, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=5, le=50),
):
    try:
        total = db_search_count(query)
        offset = (page - 1) * page_size
        results = db_search_news(query, limit=page_size, offset=offset)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": results,
                "total": total,
                "page": page,
                "page_size": page_size,
                "query": query,
                "update_time": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
    except Exception as e:
        logger.error(f"搜索新闻失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "搜索失败，请稍后重试", "data": []},
        )


@app.get("/api/export/json")
async def export_json(start_date: str = Query(None), end_date: str = Query(None)):
    """Streaming JSON export — 流式生成 JSON 数组，大数据量不下"""
    fn = f"news_{start_date or 'all'}_{end_date or 'all'}.json"

    def json_generator():
        yield "[\n"
        first = True
        for news in db_stream_news(start_date, end_date):
            if not first:
                yield ",\n"
            yield json.dumps(news, ensure_ascii=False)
            first = False
        yield "\n]"

    return StreamingResponse(
        json_generator(),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={fn}"},
    )


@app.get("/api/export/csv")
async def export_csv(start_date: str = Query(None), end_date: str = Query(None)):
    """Streaming CSV export — 流式输出 CSV，支持 Excel 直接打开"""
    import csv as csv_module

    fn = f"news_{start_date or 'all'}_{end_date or 'all'}.csv"

    def csv_generator():
        # BOM for Excel UTF-8 detection
        yield "\ufeff"
        output = io.StringIO()
        w = csv_module.writer(output)
        w.writerow(["标题", "链接", "来源", "发布时间", "摘要"])
        yield output.getvalue()

        buffer = []
        for news in db_stream_news(start_date, end_date):
            buffer.append(
                [
                    news.get("title", ""),
                    news.get("url", ""),
                    news.get("source", ""),
                    news.get("publish_time", ""),
                    (news.get("intro", "") or "")[:200],
                ]
            )
            if len(buffer) >= 100:
                output = io.StringIO()
                w = csv_module.writer(output)
                w.writerows(buffer)
                yield output.getvalue()
                buffer = []
        if buffer:
            output = io.StringIO()
            w = csv_module.writer(output)
            w.writerows(buffer)
            yield output.getvalue()

    return StreamingResponse(
        csv_generator(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={fn}"},
    )


@app.get("/api/export/md")
async def export_md(start_date: str = Query(None), end_date: str = Query(None)):
    """Streaming Markdown export — 流式输出 Markdown 表格"""
    fn = f"news_{start_date or 'all'}_{end_date or 'all'}.md"

    def md_generator():
        yield "| 标题 | 来源 | 时间 | 摘要 |\n"
        yield "| --- | --- | --- | --- |\n"
        for news in db_stream_news(start_date, end_date):
            t = news.get("title", "").replace("|", "\\|")
            s = news.get("source", "").replace("|", "\\|")
            tm = news.get("publish_time", "").replace("|", "\\|")
            i = (news.get("intro", "") or "")[:100].replace("|", "\\|")
            yield f"| {t} | {s} | {tm} | {i} |\n"

    return StreamingResponse(
        md_generator(),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={fn}"},
    )


@app.get("/api/export/check")
async def export_check(start_date: str = Query(None), end_date: str = Query(None)):
    """验证接口：使用 COUNT 查询高效获取数量，不加载数据"""
    with get_db() as conn:
        c = conn.cursor()
        query = "SELECT COUNT(*) FROM news WHERE 1=1"
        params = []
        if start_date:
            query += " AND publish_time >= ?"
            params.append(start_date)
        if end_date:
            query += " AND publish_time <= ?"
            params.append(end_date + " 23:59:59")
        c.execute(query, params)
        count = c.fetchone()[0]
    return {
        "success": True,
        "count": count,
        "date_range": f"{start_date or '最早'} ~ {end_date or '最新'}",
    }


@app.get("/api/export/dates")
async def export_dates():
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT DISTINCT substr(publish_time, 1, 10) as d FROM news ORDER BY d DESC"
        )
        dates = [row[0] for row in c.fetchall()]
    return {
        "success": True,
        "dates": dates,
        "min_date": dates[-1] if dates else None,
        "max_date": dates[0] if dates else None,
    }


@app.get("/api/export/html")
async def export_html(start_date: str = Query(None), end_date: str = Query(None)):
    """Streaming HTML export — 流式输出 HTML 表格"""
    date_range = f"{start_date or '最早'} ~ {end_date or '最新'}"
    fn = f"news_{start_date or 'all'}_{end_date or 'all'}.html"

    def html_generator():
        yield f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>财经新闻导出</title>
<style>body{{font-family:sans-serif;margin:20px;background:#f5f5f5;}}table{{border-collapse:collapse;background:#fff;width:100%;}}th{{background:#2c3e50;color:#fff;padding:10px;text-align:left;}}td{{padding:8px;border:1px solid #ddd;}}tr:nth-child(even){{background:#f9f9f9;}}</style></head>
<body><h2>财经新闻导出 - {now_bj().strftime("%Y-%m-%d %H:%M:%S")}</h2>
<p>时间范围：{html.escape(date_range)}</p>
<table><tr><th>标题</th><th>来源</th><th>时间</th><th>摘要</th></tr>
"""
        count = 0
        for news in db_stream_news(start_date, end_date):
            count += 1
            color = SOURCE_COLORS.get(news["source"], "#3498db")
            yield f"""<tr>
<td style="padding:8px;border:1px solid #ddd;">{html.escape(news["title"])}</td>
<td style="padding:8px;border:1px solid #ddd;"><span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;">{html.escape(news["source"])}</span></td>
<td style="padding:8px;border:1px solid #ddd;">{html.escape(news["publish_time"])}</td>
<td style="padding:8px;border:1px solid #ddd;">{html.escape((news.get("intro") or "")[:80])}</td>
</tr>
"""
        yield f"""</table>
<p>共 {count} 条新闻</p></body></html>"""

    return StreamingResponse(
        html_generator(),
        media_type="text/html",
        headers={"Content-Disposition": f"attachment; filename={fn}"},
    )


@app.get("/api/health")
async def health_check():
    try:
        current, _ = tracemalloc.get_traced_memory()
    except Exception:
        current = 0
    db_size_mb = (
        round(os.path.getsize(DB_PATH) / (1024 * 1024), 2)
        if os.path.exists(DB_PATH)
        else 0
    )
    return {
        "status": "healthy",
        "service": "财经新闻展示系统",
        "timestamp": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.9.0",
        "memory_kb": round(current / 1024, 2),
        "news_in_db": db_count(),
        "db_size_mb": db_size_mb,
        "source_colors": SOURCE_COLORS,
    }


@app.post("/api/news/reset")
async def reset_news():
    with get_db() as conn:
        conn.execute("DELETE FROM news")
        conn.commit()
    for k in source_last_ts:
        source_last_ts[k] = 0
    return {"success": True, "message": "已重置"}


@app.post("/api/dedup/scan")
async def dedup_scan():
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT id, title, url, simhash FROM news WHERE simhash IS NOT NULL AND simhash != '' ORDER BY publish_ts DESC, id DESC LIMIT 5000"
            )
            rows = c.fetchall()
            group_map = {}
            next_group = 1
            for row in rows:
                news_id = row["id"]
                simhash_val = (
                    int(row["simhash"], 16)
                    if isinstance(row["simhash"], str)
                    else row["simhash"]
                )
                assigned_group = 0
                for gid, members in group_map.items():
                    for member_hash in members:
                        if hamming_distance(simhash_val, member_hash) <= 10:
                            assigned_group = gid
                            break
                    if assigned_group > 0:
                        break
                if assigned_group == 0:
                    assigned_group = next_group
                    next_group += 1
                    group_map[assigned_group] = []
                group_map[assigned_group].append(simhash_val)
                c.execute(
                    "UPDATE news SET dedup_group = ? WHERE id = ?",
                    (assigned_group, news_id),
                )
            conn.commit()
            groups_found = len([g for g, m in group_map.items() if len(m) >= 2])
            news_deduplicated = sum(
                len(m) - 1 for g, m in group_map.items() if len(m) >= 2
            )
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "groups_found": groups_found,
                "news_deduplicated": news_deduplicated,
            },
        )
    except Exception as e:
        logger.error(f"去重扫描失败: {e}")
        return JSONResponse(
            status_code=500, content={"success": False, "message": str(e)}
        )


@app.get("/api/dedup/groups")
async def dedup_groups(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=5, le=100)
):
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT dedup_group, COUNT(*) as cnt FROM news WHERE dedup_group > 0 GROUP BY dedup_group HAVING cnt >= 2 ORDER BY cnt DESC"
            )
            all_groups = [dict(row) for row in c.fetchall()]
            total = len(all_groups)
            offset = (page - 1) * page_size
            page_groups = all_groups[offset : offset + page_size]
            result = []
            for g in page_groups:
                gid = g["dedup_group"]
                c.execute(
                    "SELECT id, title, url, source, publish_time FROM news WHERE dedup_group = ? ORDER BY publish_ts DESC LIMIT 10",
                    (gid,),
                )
                items = [dict(row) for row in c.fetchall()]
                result.append({"dedup_group": gid, "count": g["cnt"], "items": items})
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": result,
                "total": total,
                "page": page,
                "page_size": page_size,
            },
        )
    except Exception as e:
        logger.error(f"获取去重组失败: {e}")
        return JSONResponse(
            status_code=500, content={"success": False, "message": str(e)}
        )


@app.get("/api/dedup/stats")
async def dedup_stats():
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM news")
            total_news = c.fetchone()[0]
            c.execute(
                "SELECT COUNT(DISTINCT dedup_group) FROM news WHERE dedup_group > 0"
            )
            total_groups = c.fetchone()[0]
            c.execute(
                "SELECT COUNT(*) FROM news WHERE dedup_group > 0 AND dedup_group IN (SELECT dedup_group FROM news GROUP BY dedup_group HAVING COUNT(*) >= 2)"
            )
            dedup_news = c.fetchone()[0]
            c.execute(
                "SELECT SUM(cnt - 1) FROM (SELECT dedup_group, COUNT(*) as cnt FROM news WHERE dedup_group > 0 GROUP BY dedup_group HAVING cnt >= 2)"
            )
            cleanable = c.fetchone()[0] or 0
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "total_news": total_news,
                "total_groups": total_groups,
                "dedup_news": dedup_news,
                "cleanable": cleanable,
            },
        )
    except Exception as e:
        logger.error(f"获取去重统计失败: {e}")
        return JSONResponse(
            status_code=500, content={"success": False, "message": str(e)}
        )


@app.get("/api/dedup/group/{group_id}")
async def dedup_group_detail(group_id: int):
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT id, title, url, source, publish_time, publish_ts, intro FROM news WHERE dedup_group = ? ORDER BY publish_ts DESC",
                (group_id,),
            )
            items = [dict(row) for row in c.fetchall()]
        if not items:
            return JSONResponse(
                status_code=404, content={"success": False, "message": "组不存在"}
            )
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "dedup_group": group_id,
                "count": len(items),
                "items": items,
            },
        )
    except Exception as e:
        logger.error(f"获取去重组详情失败: {e}")
        return JSONResponse(
            status_code=500, content={"success": False, "message": str(e)}
        )


STOP_WORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没", "看", "好",
    "自己", "这", "他", "她", "它", "们", "那", "些", "及", "与", "等", "或", "但",
    "如果", "因为", "所以", "虽然", "然而", "但是", "之", "被", "把", "从", "对",
    "为", "以", "将", "还", "又", "更", "太", "非常", "十分", "最", "以及", "没有",
    "可以", "应该", "可能", "需要",
    "今日", "昨日", "明天", "今天", "目前", "已经", "还是", "只是", "不过",
    "那么", "否则", "要么", "要不", "不仅", "而且", "并且", "或者", "除了",
    "关于", "对于", "由于", "为了", "按照", "根据", "通过", "经过", "随着",
    "作为", "所谓", "来说", "而言", "来看", "上看", "下看", "出来", "下来",
    "起来", "进来", "过来", "出去", "下去", "回去", "进去", "上去",
    "表示", "报道", "据悉", "消息", "透露", "显示", "提到", "指出", "强调",
    "称", "称为", "被视为", "及其", "与否", "如此", "这样", "那样", "这么",
    "怎么", "什么", "如何", "为何", "何时", "哪里", "哪些", "多少", "为什么",
    "怎么样", "怎样", "若干", "某个", "某些", "任何", "一切", "所有", "大量",
    "一些", "一点", "部分", "大部分", "少数", "多数", "许多", "很多", "不少",
    "更多", "更少", "各", "每", "该", "本", "另", "别的", "其他", "其它", "其余",
    "整个", "全部", "全都", "大都", "大多", "一般", "通常", "往往", "常常",
    "经常", "时常", "不断", "反复", "逐步", "逐渐", "渐渐", "最终", "最后",
    "终于", "总", "总是", "始终", "一直", "一向", "从来", "历来", "向来",
    "正在", "正", "将要", "即将", "能", "能够", "应当", "必须", "值得",
    "便于", "得以", "用来", "用于",
    "10", "20", "30", "40", "50", "100",
    "3", "2", "1", "4", "5", "6", "7", "8", "9", "0",
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "by", "with", "from", "up", "about", "into", "over", "after",
    "is", "are", "was", "were", "been", "be", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "can", "could",
    "may", "might", "shall", "should",
    "its", "it's", "it", "this", "that", "these", "those",
    "not", "no", "nor", "as", "at", "so", "if", "than", "then",
    "just", "also", "very", "too", "more", "most", "some", "any", "each", "every",
    "all", "both", "few", "such", "which", "what", "when", "where", "how",
    "who", "whom", "why", "here", "there",
    "their", "them", "they", "we", "our", "your", "us",
    "out", "off", "down", "only", "own", "same", "while", "now",
    "new", "old", "one", "two", "first", "last", "next",
    "other", "another", "much", "many", "well", "back", "still", "even", "yet",
    "already", "ago", "ever", "never", "before", "after", "above", "below",
    "per", "via", "vs", "vs.", "inc", "inc.", "ltd", "ltd.", "co", "co.",
    "corp", "dept", "est", "etc",
}


@app.get("/api/trending")
async def trending():
    now_ts = time.time()
    if now_ts < _trending_cache["expires_at"]:
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": _trending_cache["data"],
                "updated_at": _trending_cache["updated_at"],
            },
        )
    threshold = int(time.time()) - 86400
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT title FROM news WHERE publish_ts > ?", (threshold,))
            titles = [row[0] for row in c.fetchall() if row[0]]
    except Exception as e:
        logger.error(f"趋势查询失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "查询失败"},
        )
    word_counter: Counter = Counter()
    try:
        import jieba

        for title in titles:
            words = jieba.cut(title)
            for w in words:
                w = w.strip()
                if not w:
                    continue
                if w.isdigit():
                    continue
                if w.isascii() and len(w) < 3:
                    continue
                if len(w) >= 2 and w not in STOP_WORDS:
                    word_counter[w] += 1
    except ImportError:
        for title in titles:
            n = 2
            for i in range(len(title) - n + 1):
                ng = title[i : i + n]
                ng = ng.strip()
                if not ng:
                    continue
                if ng.isdigit():
                    continue
                if ng.isascii() and len(ng) < 3:
                    continue
                if ng and ng not in STOP_WORDS:
                    word_counter[ng] += 1
    top_words = [{"word": w, "count": c} for w, c in word_counter.most_common(30)]
    updated_at = now_bj().strftime("%Y-%m-%d %H:%M:%S")
    _trending_cache["data"] = top_words
    _trending_cache["updated_at"] = updated_at
    _trending_cache["expires_at"] = now_ts + TRENDING_CACHE_TTL
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "data": top_words,
            "updated_at": updated_at,
        },
    )


TIMELINE_CATEGORIES = ["国际热点", "国内热点", "社会热点", "行业热点", "公司热点", "个股公告"]


@app.get("/api/timeline")
async def get_timeline(category: str = Query(None)):
    data = _TIMELINE_DATA_CACHE["data"]
    if not data:
        data = []
    if category:
        cats = [c.strip() for c in category.split(",")]
        data = [e for e in data if e["category"] in cats]
    stats = {"total": len(_TIMELINE_DATA_CACHE["data"]), "filtered": len(data)}
    return JSONResponse(
        status_code=200,
        content={"success": True, "data": data, "source": "merged", "stats": stats},
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.add(websocket)
    try:
        await websocket.send_json({"type": "connected", "message": "connected"})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        active_connections.discard(websocket)


if __name__ == "__main__":
    db_cleanup_if_needed()
    db_backfill_publish_ts()
    db_backfill_dedup_fields()

    import asyncio
    import sys

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    port = int(os.environ.get("PORT", 10842))
    import uvicorn

    uvicorn.run(
        app, host="0.0.0.0", port=port, reload=False, workers=1, log_level="info"
    )
