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
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from contextlib import asynccontextmanager
from collections import Counter, OrderedDict
from urllib.parse import quote, urlencode

import nvidia_client
import fuzzy_search

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

if os.environ.get("TRACE_MALLOC"):
    import tracemalloc

    tracemalloc.start()

_re_highlight_cache: OrderedDict[str, re.Pattern] = OrderedDict()
_RE_HIGHLIGHT_CACHE_MAX = 500


def _get_highlight_pattern(query: str) -> re.Pattern:
    if query not in _re_highlight_cache:
        if len(_re_highlight_cache) >= _RE_HIGHLIGHT_CACHE_MAX:
            _re_highlight_cache.popitem(last=False)
        _re_highlight_cache[query] = re.compile(re.escape(query), re.IGNORECASE)
    return _re_highlight_cache[query]


TZ_BJ = timezone(timedelta(hours=8))


def now_bj() -> datetime:
    return datetime.now(TZ_BJ).replace(tzinfo=None)


def is_trading_day() -> bool:
    weekday = now_bj().weekday()
    return weekday < 5


def is_trading_hours() -> bool:
    if not is_trading_day():
        return False
    bj = now_bj()
    hour, minute = bj.hour, bj.minute
    total_minutes = hour * 60 + minute
    morning_start = 9 * 60
    morning_end = 11 * 60 + 30
    afternoon_start = 13 * 60
    afternoon_end = 15 * 60
    return morning_start <= total_minutes <= morning_end or afternoon_start <= total_minutes <= afternoon_end


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
            ts = int(dt.replace(tzinfo=TZ_BJ).timestamp())
            if ts > int(now.replace(tzinfo=TZ_BJ).timestamp()):
                dt = dt.replace(year=dt.year - 1)
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
    "GDELT": 10.0,  # 安全余量，实际限制为每 IP 每 5 秒 1 次
}
_last_source_req: dict[str, float] = {}  # 各来源上次请求时间戳
_rate_blocked_until: dict[str, float] = {}  # 各来源被限速后的冷却截止时间戳

# --- AI 热点分析 ---
_AI_TRENDING_INTERVAL = 600  # 10分钟
_ai_trending_cache: dict = {
    "data": [],
    "updated_at": "",
    "ai_generated": False,
    "frozen": False,
}
_ai_analysis_in_progress = False
_last_clear_date = ""


async def _daily_clear_task():
    """交易日9:00清空，13:00解除午间冻结"""
    global _last_clear_date
    while True:
        bj = now_bj()
        today_str = bj.strftime("%Y-%m-%d")
        if (
            bj.hour == 9
            and bj.minute == 0
            and is_trading_day()
            and _last_clear_date != today_str
        ):
            _ai_trending_cache["data"] = []
            _ai_trending_cache["updated_at"] = today_str + " 09:00:00"
            _ai_trending_cache["ai_generated"] = False
            _ai_trending_cache["frozen"] = False
            _last_clear_date = today_str
            logger.info(f"AI热点已清空，解除冻结，开始新交易日: {today_str}")
        if bj.hour == 13 and bj.minute == 0 and is_trading_day():
            if _ai_trending_cache.get("frozen"):
                _ai_trending_cache["frozen"] = False
                logger.info("下午开盘，AI热点分析已重新启用")
        await asyncio.sleep(30)


async def _do_ai_trending_analysis():
    """调用 NVIDIA AI 分析当前新闻热点"""
    global _ai_analysis_in_progress
    if _ai_analysis_in_progress:
        return
    _ai_analysis_in_progress = True
    try:
        threshold = int(time.time()) - 86400
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT title, intro, source FROM news WHERE publish_ts > ? ORDER BY publish_ts DESC LIMIT 200",
                (threshold,),
            )
            rows = c.fetchall()
        if not rows:
            logger.info("AI热点分析: 无新闻数据")
            return

        news_text = "\n".join(
            f"- 【{r['source']}】{r['title']} {r['intro'] or ''}"[:200]
            for r in rows
        )

        system_prompt = """你是一位关键词提取工具。从以下财经新闻原文中提取8-12个最具热度的关键词，严格按规则执行。

## 核心规则（违反即无效）
1. 关键词必须**逐字来源于**下方新闻原文（标题或导语），不得自行创造、改写或概括
2. 禁止对新闻内容进行任何形式的分析、总结、归纳或抽象化处理
3. 关键词必须是具体可搜索的实体词——用户在搜索引擎中输入该词必须能直接找到相关新闻

## 合格关键词示例（必须是原文中出现的词）
公司名称：宁德时代、贵州茅台、英伟达、特斯拉
产品名称：问界M9、iPhone 16、GPT-5、HBM
指数名称：上证指数、恒生科技指数、纳斯达克
人名/机构：特朗普、鲍威尔、美联储、国务院
政策/法规：以旧换新、新国九条、降准、特别国债
行业/技术术语：固态电池、量子计算、光伏、HBM
地名/区域：中东、东南亚、长三角

## 严格禁止使用的词汇（出现即视为无效输出）
市场动态、政策调整、经济增长、行业趋势、重磅发布、最新消息
数据表现、市场变化、热点轮动、资金流向、上市公司公告、经济数据
行业利好、政策加码、市场震荡、板块轮动、结构行情、行情回顾

## 输出格式
{"hot_topics": [{"topic": "关键词（必须与原文用词完全一致）", "description": "从原文直接摘录的包含该关键词的片段（不超过15字）", "count": 根据新闻中出现频率估算的热度整数}, ...]}"""

        user_prompt = f"以下为今日财经新闻原文，请从中直接提取关键词，不得分析概括：\n\n{news_text}"

        content = await nvidia_client.call_nvidia(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=2048,
        )

        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
            content = content.rsplit("```", 1)[0]
        content = content.strip()

        parsed = json.loads(content)
        hot_topics = parsed.get("hot_topics", [])
        if not isinstance(hot_topics, list) or not hot_topics:
            logger.warning("AI返回的热点列表为空")
            return

        _ai_trending_cache["data"] = hot_topics
        _ai_trending_cache["updated_at"] = now_bj().strftime("%Y-%m-%d %H:%M:%S")
        _ai_trending_cache["ai_generated"] = True
        logger.info(f"AI热点分析完成: {len(hot_topics)} 个热点")

    except json.JSONDecodeError as e:
        logger.error(f"AI热点分析JSON解析失败: {e}")
    except Exception as e:
        logger.error(f"AI热点分析异常: {e}")
    finally:
        _ai_analysis_in_progress = False


async def _ai_trending_analysis_loop():
    """交易时间每10分钟分析一次，11:30/15:00收盘后冻结"""
    await asyncio.sleep(15)

    bj = now_bj()
    total_min = bj.hour * 60 + bj.minute
    morning = 540 <= total_min <= 689
    afternoon = 780 <= total_min < 900

    if is_trading_day() and (morning or afternoon):
        await _do_ai_trending_analysis()
    else:
        await _do_ai_trending_analysis()
        if _ai_trending_cache["data"]:
            _ai_trending_cache["frozen"] = True
            logger.info("非交易时段，热点已分析并冻结")

    while True:
        try:
            if _ai_trending_cache.get("frozen"):
                await asyncio.sleep(60)
                continue

            bj = now_bj()
            total_min = bj.hour * 60 + bj.minute
            morning = 540 <= total_min <= 689
            afternoon_open = 780 <= total_min < 900
            morning_just_closed = 690 <= total_min <= 700
            afternoon_just_closed = 900 <= total_min <= 910

            if morning:
                await _do_ai_trending_analysis()
            elif morning_just_closed:
                await _do_ai_trending_analysis()
                _ai_trending_cache["frozen"] = True
                logger.info("午间收盘AI分析完成，热点已冻结")
            elif afternoon_open:
                await _do_ai_trending_analysis()
            elif afternoon_just_closed:
                await _do_ai_trending_analysis()
                _ai_trending_cache["frozen"] = True
                logger.info("收盘AI分析完成，热点已冻结")

            await asyncio.sleep(_AI_TRENDING_INTERVAL)
        except Exception as e:
            logger.error(f"AI热点分析循环异常: {e}")
            await asyncio.sleep(_AI_TRENDING_INTERVAL)


def _log_task_death(task_name: str):
    def _cb(task: asyncio.Task):
        if task.cancelled():
            return
        exc = task.exception()
        if exc and not isinstance(exc, asyncio.CancelledError):
            logger.error(f"后台任务 [{task_name}] 异常终止: {exc}")
    return _cb


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(_background_fetch_loop()),
        asyncio.create_task(_timeline_startup_build()),
        asyncio.create_task(_ai_trending_analysis_loop()),
        asyncio.create_task(_daily_clear_task()),
        asyncio.create_task(_event_calendar_update_loop()),
        asyncio.create_task(_event_calendar_startup_build()),
    ]
    names = [
        "background_fetch", "timeline_startup", "ai_trending",
        "daily_clear", "event_calendar_update", "event_calendar_startup",
    ]
    for t, n in zip(tasks, names):
        t.add_done_callback(_log_task_death(n))
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
        _db_conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
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
                created_at TEXT DEFAULT (datetime('now','localtime')),
                event_type TEXT DEFAULT 'general',
                country TEXT DEFAULT 'CN',
                symbol TEXT,
                verified INTEGER DEFAULT 0,
                data_sources TEXT,
                fetched_at TEXT
            )
        """)
        for col, ctype in [
            ("event_type", "TEXT"),
            ("country", "TEXT"),
            ("symbol", "TEXT"),
            ("verified", "INTEGER"),
            ("data_sources", "TEXT"),
            ("fetched_at", "TEXT"),
        ]:
            try:
                c.execute(f"ALTER TABLE timeline_events ADD COLUMN {col} {ctype}")
            except Exception:
                pass
        c.execute("""
            CREATE TABLE IF NOT EXISTS event_calendar_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_date TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '社会热点',
                event_type TEXT DEFAULT 'general',
                importance INTEGER DEFAULT 2,
                description TEXT,
                source TEXT,
                source_url TEXT,
                country TEXT DEFAULT 'CN',
                symbol TEXT,
                verified INTEGER DEFAULT 0,
                data_sources TEXT,
                fetched_at TEXT,
                event_hash TEXT UNIQUE,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_ecc_event_date ON event_calendar_cache(event_date ASC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ecc_event_type ON event_calendar_cache(event_type)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ecc_verified ON event_calendar_cache(verified)")
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


def db_search_news_fuzzy_candidates(query, limit=500):
    with get_db() as conn:
        c = conn.cursor()
        fourteen_days_ago = int(time.time()) - 14 * 86400
        conditions = ["n.publish_ts > ?"]
        params = [fourteen_days_ago]
        query_norm = re.sub(r'\s+', '', query).lower().strip()
        if query_norm:
            def _escape_like(s):
                return s.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            char_conditions = []
            for ch in query_norm:
                if '\u4e00' <= ch <= '\u9fff' or (ch.isalpha() and len(ch) == 1):
                    char_conditions.append("(lower(n.title) LIKE ? OR lower(n.intro) LIKE ?)")
                    params.extend([f'%{_escape_like(ch)}%', f'%{_escape_like(ch)}%'])
            if char_conditions:
                conditions.append("(" + " OR ".join(char_conditions) + ")")
        where_clause = " AND ".join(conditions)
        c.execute(
            f"""SELECT n.title, n.url, n.source, n.publish_time, n.publish_ts, n.intro, n.dedup_group,
               COALESCE((SELECT COUNT(*) FROM news n2 WHERE n2.dedup_group = n.dedup_group AND n2.dedup_group > 0), 1) AS dedup_count
               FROM news n
               WHERE {where_clause}
               ORDER BY n.publish_ts DESC, n.id DESC
               LIMIT ?""",
            params + [limit],
        )
        return [dict(row) for row in c.fetchall()]


_COLUMNS_SEARCH = """n.title, n.url, n.source, n.publish_time, n.publish_ts, n.intro, n.dedup_group,
    COALESCE((SELECT COUNT(*) FROM news n2 WHERE n2.dedup_group = n.dedup_group AND n2.dedup_group > 0), 1) AS dedup_count"""


def db_search_news(query, limit=10, offset=0, fuzzy=True):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            f"""
            SELECT {_COLUMNS_SEARCH}
            FROM news n
            WHERE instr(lower(n.title), lower(?)) OR instr(lower(n.intro), lower(?)) OR instr(lower(n.source), lower(?))
            ORDER BY n.publish_ts DESC, n.id DESC
            LIMIT ? OFFSET ?
        """,
            (query, query, query, limit, offset),
        )
        exact_rows = [dict(row) for row in c.fetchall()]

    if fuzzy and len(exact_rows) == 0:
        cached = fuzzy_search.get_cached_fuzzy(query, fuzzy_search.FUZZY_DEFAULT_THRESHOLD)
        if cached is not None:
            fuzzy_rows = cached[0]
        else:
            candidates = db_search_news_fuzzy_candidates(query)
            fuzzy_rows = fuzzy_search.filter_fuzzy_results(query, candidates)
            fuzzy_search.set_cached_fuzzy(query, fuzzy_search.FUZZY_DEFAULT_THRESHOLD, fuzzy_rows)

        exact_rows = fuzzy_rows[offset:offset + limit]

    highlight_pattern = _get_highlight_pattern(query)
    for row in exact_rows:
        title = row["title"]
        intro = row["intro"] or ""
        row["title_highlight"] = highlight_pattern.sub(
            lambda m, t=title: f"<mark>{m.group(0)}</mark>", title
        )
        row["intro_highlight"] = highlight_pattern.sub(
            lambda m, i=intro: f"<mark>{m.group(0)}</mark>", intro
        )

    return exact_rows


def db_search_count(query, fuzzy=True):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT COUNT(*) FROM news
            WHERE instr(lower(title), lower(?)) OR instr(lower(intro), lower(?)) OR instr(lower(source), lower(?))
        """,
            (query, query, query),
        )
        exact_count = c.fetchone()[0]

    if fuzzy and exact_count == 0:
        cached = fuzzy_search.get_cached_fuzzy(query, fuzzy_search.FUZZY_DEFAULT_THRESHOLD)
        if cached is not None:
            fuzzy_count = len(cached[0])
        else:
            candidates = db_search_news_fuzzy_candidates(query, limit=500)
            fuzzy_results = fuzzy_search.filter_fuzzy_results(query, candidates, max_results=200)
            fuzzy_search.set_cached_fuzzy(query, fuzzy_search.FUZZY_DEFAULT_THRESHOLD, fuzzy_results)
            fuzzy_count = len(fuzzy_results)
        return fuzzy_count

    return exact_count


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
    "企查查": 0,
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
    "企查查": "#E85A3A",  # 橙红 ~10°
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
        "url": "https://www.cls.cn/v1/roll/get_roll_list",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://www.cls.cn/telegraph",
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
    {
        "name": "企查查",
        "url": "http://rss.qcc.com:9000/news-and-flash",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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

    # 冷却检查：被 429 限速后跳过该来源，避免反复撞墙
    blocked_until = _rate_blocked_until.get(source_name, 0)
    if blocked_until > time.time():
        remaining = int(blocked_until - time.time())
        logger.info(f"{source_name} 仍在冷却中，跳过（剩余 {remaining}s）")
        return news_list

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

            # 财联社需要签名认证: 参数排序 -> urlencode -> sha1 -> md5
            if source_name == "财联社":
                cls_params = {
                    "app": "CailianpressWeb",
                    "os": "web",
                    "sv": "8.4.6",
                    "rn": "20",
                    "last_time": str(int(last_ts if last_ts > 0 else time.time())),
                }
                qs = urlencode(sorted(cls_params.items()))
                cls_params["sign"] = hashlib.md5(
                    hashlib.sha1(qs.encode()).hexdigest().encode()
                ).hexdigest()
                kwargs["params"] = cls_params

            if method == "POST":
                response = await client.post(**kwargs)
            else:
                response = await client.get(**kwargs)

            # 记录请求时间（用于速率限制）
            if min_interval > 0:
                _last_source_req[source_name] = time.time()

            if response.status_code == 429:
                retry_after_str = (response.headers.get("Retry-After") or "").strip()
                retry_after = int(retry_after_str) if retry_after_str.isdigit() else 60
                logger.warning(
                    f"{source_name} 触发速率限制 (429)，冷却 {retry_after}s"
                )
                # 标记冷却截止时间，后续周期跳过该来源（GDELT block 可能持续 15 分钟）
                _rate_blocked_until[source_name] = time.time() + retry_after + 30
                return news_list

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
                    pt = now_bj().strftime("%Y-%m-%d %H:%M:%S")
                    try:
                        if seendate:
                            dt = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ")
                            dt = dt.replace(tzinfo=timezone.utc)
                            ts = int(dt.timestamp())
                            pt = bj_str_from_ts(ts)
                    except (ValueError, TypeError):
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

            # 企查查 - RSS XML
            elif source_name == "企查查":
                soup = BeautifulSoup(response.text, "xml")
                items = soup.find_all("item")
                for item in items:
                    title_tag = item.find("title")
                    link_tag = item.find("link")
                    pub_date_tag = item.find("pubDate")
                    desc_tag = item.find("description")

                    title = (title_tag.text if title_tag else "").strip()
                    if not title:
                        continue

                    link = link_tag.text if link_tag else "#"
                    if link and not link.startswith("http"):
                        link = f"https://news.qcc.com{link}"

                    ts = 0
                    pt = now_bj().strftime("%Y-%m-%d %H:%M:%S")
                    pub_date = (pub_date_tag.text if pub_date_tag else "").strip()
                    if pub_date:
                        ts = ts_from_bj_str(pub_date)
                        if ts:
                            pt = bj_str_from_ts(ts)

                    if ts <= last_ts:
                        continue

                    intro = ""
                    if desc_tag and desc_tag.text:
                        desc_soup = BeautifulSoup(desc_tag.text, "lxml")
                        intro = desc_soup.get_text(strip=True)[:150]
                        intro = re.sub(r"\s+", " ", intro).strip()

                    news_list.append(
                        {
                            "title": title[:80],
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
    # GDELT 每分钟最多请求一次（成功后也设置冷却，不阻塞其他来源的并行 gather）
    if source_name == "GDELT":
        _rate_blocked_until[source_name] = time.time() + 60
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
                    "event_type": EVENT_TYPE_IPO,
                    "country": "CN",
                    "symbol": item.get("SECURITY_CODE", ""),
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
                    "event_type": EVENT_TYPE_REGULATORY if "公告" in title else EVENT_TYPE_GENERAL,
                    "country": "CN",
                    "symbol": "",
                })
            logger.info(f"新浪公告爬取完成: {len(events)} 条")
    except Exception as e:
        logger.warning(f"新浪公告爬取失败: {e}")
    return events


def _insert_timeline_events(events: list):
    if not events:
        return
    now_str = now_bj().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        c = conn.cursor()
        for ev in events:
            event_hash = hashlib.md5(
                f"{ev['date']}|{ev['title'][:40]}|{ev['category']}".encode()
            ).hexdigest()[:16]
            try:
                c.execute(
                    """INSERT OR IGNORE INTO timeline_events
                       (event_date, title, category, importance, description, source, source_url, event_hash,
                        event_type, country, symbol, verified, data_sources, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ev["date"],
                        ev["title"],
                        ev["category"],
                        ev.get("importance", 2),
                        ev.get("description", ""),
                        ev.get("source", "crawler"),
                        ev.get("source_url", ""),
                        event_hash,
                        ev.get("event_type", EVENT_TYPE_GENERAL),
                        ev.get("country", "CN"),
                        ev.get("symbol", ""),
                        ev.get("verified", 0),
                        ev.get("data_sources", ""),
                        ev.get("fetched_at", now_str),
                    ),
                )
            except Exception as e:
                logger.warning(f"插入时间线事件失败: {e}")
        conn.commit()


def _load_timeline_from_db() -> list:
    events = []
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                """SELECT event_date, title, category, importance, description, source, source_url,
                          event_type, country, symbol, verified, data_sources
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
                    "event_type": row["event_type"] if "event_type" in row.keys() else EVENT_TYPE_GENERAL,
                    "country": row["country"] if "country" in row.keys() else "CN",
                    "symbol": row["symbol"] if "symbol" in row.keys() else "",
                    "verified": row["verified"] if "verified" in row.keys() else 0,
                    "data_sources": row["data_sources"] if "data_sources" in row.keys() else "",
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


_timeline_build_counter = 0


async def _background_fetch_loop():
    global _timeline_build_counter
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
                for ws in list(active_connections):
                    try:
                        await ws.send_text(message)
                    except Exception:
                        disconnected.add(ws)
                active_connections.difference_update(disconnected)
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
            _timeline_build_counter += 1
            if _timeline_build_counter >= 10:
                _timeline_build_counter = 0
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
    fuzzy: bool = Query(True),
):
    try:
        total = db_search_count(query, fuzzy=fuzzy)
        offset = (page - 1) * page_size
        results = db_search_news(query, limit=page_size, offset=offset, fuzzy=fuzzy)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": results,
                "total": total,
                "page": page,
                "page_size": page_size,
                "query": query,
                "fuzzy": fuzzy,
                "update_time": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
    except Exception as e:
        logger.error(f"搜索新闻失败: {e}")
        return JSONResponse(
            status_code=500, content={"success": False, "message": "搜索失败，请稍后重试", "data": []},
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
                "SELECT id, title, url, simhash FROM news WHERE simhash IS NOT NULL AND simhash != '' ORDER BY simhash ASC LIMIT 5000"
            )
            rows = c.fetchall()
            group_map = {}
            next_group = 1
            assigned = {}
            window_size = 50
            for i, row in enumerate(rows):
                news_id = row["id"]
                simhash_val = (
                    int(row["simhash"], 16)
                    if isinstance(row["simhash"], str)
                    else row["simhash"]
                )
                assigned_group = 0
                start = max(0, i - window_size)
                end = min(len(rows), i + window_size + 1)
                for j in range(start, end):
                    if j == i:
                        continue
                    other_id = rows[j]["id"]
                    if other_id in assigned:
                        other_simhash = (
                            int(rows[j]["simhash"], 16)
                            if isinstance(rows[j]["simhash"], str)
                            else rows[j]["simhash"]
                        )
                        if hamming_distance(simhash_val, other_simhash) <= 10:
                            assigned_group = assigned[other_id]
                            break
                if assigned_group == 0:
                    assigned_group = next_group
                    next_group += 1
                    group_map[assigned_group] = []
                group_map[assigned_group].append(simhash_val)
                assigned[news_id] = assigned_group
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
    ai_data = _ai_trending_cache["data"]
    if ai_data:
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": ai_data,
                "updated_at": _ai_trending_cache["updated_at"],
                "ai_generated": True,
            },
        )

    now_ts = time.time()
    if now_ts < _trending_cache["expires_at"]:
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": _trending_cache["data"],
                "updated_at": _trending_cache["updated_at"],
                "ai_generated": False,
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
            "ai_generated": False,
        },
    )


# --- 事件日历 (AI + WebSearch) ---
_EVENT_CALENDAR_CACHE: dict = {
    "data": [],
    "updated_at": "",
}
_event_calendar_in_progress = False


def _map_yiqiliu_category(cat_display: str) -> str:
    _MAP = {
        "体育赛事": "国际热点",
        "娱乐活动": "社会热点",
        "科技发布": "行业热点",
        "传统节日": "社会热点",
        "天文现象": "国际热点",
        "经济财经": "国内热点",
        "教育考试": "社会热点",
        "健康医疗": "社会热点",
        "环保气候": "国际热点",
        "文化艺术": "社会热点",
    }
    return _MAP.get(cat_display, "社会热点")


def _map_yiqiliu_importance(imp: str) -> int:
    _MAP = {"high": 3, "medium": 2, "low": 1}
    return _MAP.get(imp, 2)


def _get_source_url(event: dict) -> str:
    sources = event.get("sources")
    if isinstance(sources, list) and sources:
        for s in sources:
            url = s.get("url", "")
            if url:
                return url
    return f"https://www.yiqiliu.com/calendar/event/{event.get('id', '')}"


async def fetch_yiqiliu_calendar_events() -> list:
    """爬取一起六事件日历API（含分页），返回结构化事件列表"""
    all_events = []
    today = now_bj().strftime("%Y-%m-%d")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            for page in range(1, 10):
                r = await c.get(
                    "https://www.yiqiliu.com/calendar/api/timeline",
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.yiqiliu.com/"},
                    params={
                        "page": page,
                        "limit": 20,
                        "timeRange": "all",
                        "category": "all",
                        "importance": "all",
                    },
                )
                if r.status_code != 200:
                    break
                body = r.json()
                if not body.get("success"):
                    break
                events = body.get("events", [])
                if not events:
                    break
                for ev in events:
                    date_str = (ev.get("startDate") or "")[:10]
                    if not date_str or date_str < today:
                        continue
                    all_events.append({
                        "date": date_str,
                        "title": ev.get("title", "")[:80],
                        "description": ev.get("description", "")[:200],
                        "category": _map_yiqiliu_category(ev.get("categoryDisplayName", "")),
                        "importance": _map_yiqiliu_importance(ev.get("importance", "")),
                        "source_url": _get_source_url(ev),
                        "source": "yiqiliu_calendar",
                        "event_type": EVENT_TYPE_GENERAL,
                        "country": "CN",
                        "symbol": "",
                    })
                pagination = body.get("pagination", {})
                total_pages = (pagination.get("total") or 1) if pagination else 1
                if page >= total_pages:
                    break
            logger.info(f"一起六事件日历爬取完成: {len(all_events)} 条")
    except Exception as e:
        logger.warning(f"一起六事件日历爬取失败: {e}")
    return all_events


def _map_postproxy_category(ev_type: str, countries: list) -> str:
    if ev_type == "public_holiday":
        return "国内热点" if countries == ["CN"] else "国际热点"
    if ev_type in ("sporting_event",):
        return "国际热点"
    if ev_type in ("commerce_event",):
        return "行业热点"
    if ev_type in ("awareness_day", "remembrance", "religious_event", "seasonal"):
        return "社会热点"
    return "社会热点"


def _map_postproxy_importance(ev_type: str) -> int:
    _MAP = {
        "sporting_event": 3,
        "commerce_event": 3,
        "public_holiday": 2,
        "cultural_event": 2,
        "awareness_day": 1,
        "fun_holiday": 1,
        "religious_event": 2,
        "remembrance": 1,
        "seasonal": 1,
    }
    return _MAP.get(ev_type, 1)


POSTPROXY_RELEVANT_TYPES = {
    "sporting_event", "cultural_event", "commerce_event",
    "public_holiday", "awareness_day",
}


async def fetch_postproxy_calendar_events() -> list:
    """爬取PostProxy全局事件日历API，返回结构化事件列表（补充源）"""
    all_events = []
    today = now_bj().strftime("%Y-%m-%d")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(
                "https://api.postproxy.dev/api/calendar",
                headers={"User-Agent": "Mozilla/5.0"},
                params={"from": today, "per_page": 200},
            )
            if r.status_code != 200:
                logger.warning(f"PostProxy日历请求失败: {r.status_code}")
                return []
            body = r.json()
            events = body.get("data", [])
            if not events:
                return []
            for ev in events:
                ev_type = ev.get("type", "")
                if ev_type not in POSTPROXY_RELEVANT_TYPES:
                    continue
                date_str = ev.get("date", "")[:10]
                if not date_str or date_str < today:
                    continue
                title = ev.get("name", "")
                if not title:
                    continue
                all_events.append({
                    "date": date_str,
                    "title": title[:80],
                    "description": "",
                    "category": _map_postproxy_category(ev_type, ev.get("countries", [])),
                    "importance": _map_postproxy_importance(ev_type),
                    "source_url": f"https://api.postproxy.dev/api/calendar?date={date_str}",
                    "source": "postproxy_calendar",
                    "event_type": EVENT_TYPE_GENERAL,
                    "country": "CN" if ev.get("countries") == ["CN"] else "GLOBAL",
                    "symbol": "",
                })
            logger.info(f"PostProxy全局日历爬取完成: {len(all_events)} 条")
    except Exception as e:
        logger.warning(f"PostProxy全局日历爬取失败: {e}")
    return all_events


async def fetch_chinese_holidays_and_trading_calendar() -> list:
    """
    从多个权威数据源获取中国法定节假日和A股交易日历
    数据来源:
      1. holiday-cn GitHub (NateScarlet/holiday-cn) - 包含完整节假日+调休数据
      2. holiday.ailcc.com (备用) - 免费节假日API

    生成的事件类型:
      - 法定节假日（含休市提醒，重要性3）
      - 调休上班日提醒（重要性2）
      - 周末休市提醒（重要性1）
    """
    all_events = []
    today = now_bj().date()
    end_date = today + timedelta(days=60)
    today_str = today.strftime("%Y-%m-%d")
    end_date_str = end_date.strftime("%Y-%m-%d")

    holidays_by_date = {}
    makeup_dates = set()

    for year in range(today.year, end_date.year + 1):
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
                r = await c.get(
                    f"https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json",
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Accept": "application/json",
                    },
                )
                if r.status_code != 200:
                    continue
                data = r.json()
                for day in data.get("days", []):
                    date_str = day.get("date", "")
                    name = day.get("name", "").strip()
                    is_off = day.get("isOffDay", False)
                    if not date_str or date_str < today_str or date_str > end_date_str:
                        continue
                    if not name:
                        continue
                    if is_off:
                        holidays_by_date[date_str] = name
                    else:
                        makeup_dates.add(date_str)
                logger.info(
                    f"法定节假日数据(holiday-cn, {year})加载完成: "
                    f"{len([d for d in data.get('days', []) if d.get('isOffDay') and d.get('name')])} 个节假日"
                )
        except Exception as e:
            logger.warning(f"法定节假日holiday-cn({year})抓取失败: {e}")

    if not holidays_by_date:
        try:
            for year in range(today.year, end_date.year + 1):
                async with httpx.AsyncClient(timeout=15) as c:
                    r = await c.get(
                        f"https://holiday.ailcc.com/api/holiday/year/{year}",
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    if r.status_code == 200:
                        data = r.json()
                        if data.get("code") == 0:
                            for key, info in data.get("holiday", {}).items():
                                date_str = info.get("date", "")
                                name = info.get("name", "")
                                is_holiday = info.get("holiday", False)
                                if date_str and today_str <= date_str <= end_date_str:
                                    if is_holiday and name:
                                        holidays_by_date[date_str] = name
                            logger.info(f"法定节假日备用源(ailcc, {year})加载完成")
        except Exception as e:
            logger.warning(f"法定节假日备用源抓取失败: {e}")

    for date_str, name in sorted(holidays_by_date.items()):
        all_events.append({
            "date": date_str,
            "title": f"{name} - 法定节假日",
            "description": f"{name}假期，A股市场休市",
            "category": "国内热点",
            "importance": 3,
            "source_url": "https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm",
            "source": "chinese_holiday",
            "event_type": EVENT_TYPE_HOLIDAY,
            "country": "CN",
            "symbol": "",
        })

    for date_str in sorted(makeup_dates):
        if date_str >= today_str and date_str <= end_date_str:
            all_events.append({
                "date": date_str,
                "title": "调休上班日",
                "description": "调休上班日，A股正常开市交易",
                "category": "国内热点",
                "importance": 2,
                "source_url": "",
                "source": "chinese_holiday",
                "event_type": EVENT_TYPE_HOLIDAY,
                "country": "CN",
                "symbol": "",
            })

    logger.info(
        f"中国节假日/A股休市日数据生成完成: "
        f"{len(holidays_by_date)}个节假日, {len(makeup_dates)}个调休日, "
        f"共{len(all_events)}条事件"
    )
    return all_events


EVENT_TYPE_EARNINGS = "earnings"
EVENT_TYPE_ECONOMIC = "economic_indicator"
EVENT_TYPE_CENTRAL_BANK = "central_bank"
EVENT_TYPE_CORPORATE_ACTION = "corporate_action"
EVENT_TYPE_IPO = "ipo"
EVENT_TYPE_REGULATORY = "regulatory"
EVENT_TYPE_CONFERENCE = "conference"
EVENT_TYPE_HOLIDAY = "holiday"
EVENT_TYPE_GENERAL = "general"

EVENT_TYPE_LABELS = {
    EVENT_TYPE_EARNINGS: "财报",
    EVENT_TYPE_ECONOMIC: "经济指标",
    EVENT_TYPE_CENTRAL_BANK: "央行政策",
    EVENT_TYPE_CORPORATE_ACTION: "公司行为",
    EVENT_TYPE_IPO: "新股",
    EVENT_TYPE_REGULATORY: "监管公告",
    EVENT_TYPE_CONFERENCE: "会议论坛",
    EVENT_TYPE_HOLIDAY: "节假日",
    EVENT_TYPE_GENERAL: "通用",
}

SOURCE_PRIORITY = {
    "eastmoney_earnings": 10,
    "eastmoney_economic": 9,
    "eastmoney_corporate_action": 8,
    "jin10_calendar": 7,
    "10jqka_calendar": 6,
    "yiqiliu_calendar": 5,
    "postproxy_calendar": 4,
    "chinese_holiday": 3,
    "ipo_calendar": 2,
    "sina_announcement": 1,
    "ai_extracted": 0,
    "manual": 10,
}

_SOURCE_HEALTH: dict[str, dict] = {}


def _record_source_result(source_name: str, success: bool, count: int = 0, elapsed: float = 0.0):
    if source_name not in _SOURCE_HEALTH:
        _SOURCE_HEALTH[source_name] = {
            "consecutive_failures": 0,
            "last_success": None,
            "last_failure": None,
            "last_count": 0,
            "last_elapsed": 0.0,
            "degraded": False,
            "skip_cycles": 0,
        }
    health = _SOURCE_HEALTH[source_name]
    if success:
        health["consecutive_failures"] = 0
        health["last_success"] = now_bj().strftime("%Y-%m-%d %H:%M:%S")
        health["last_count"] = count
        health["last_elapsed"] = round(elapsed, 2)
        health["degraded"] = False
        health["skip_cycles"] = 0
    else:
        health["consecutive_failures"] += 1
        health["last_failure"] = now_bj().strftime("%Y-%m-%d %H:%M:%S")
        if health["consecutive_failures"] >= 3:
            health["degraded"] = True
            health["skip_cycles"] = 2


def _is_source_available(source_name: str) -> bool:
    health = _SOURCE_HEALTH.get(source_name)
    if not health:
        return True
    if health["degraded"]:
        if health["skip_cycles"] > 0:
            health["skip_cycles"] -= 1
            return False
        return True
    return True


async def fetch_earnings_calendar() -> list:
    t0 = time.time()
    events = []
    today = now_bj().strftime("%Y-%m-%d")
    end_date = (now_bj() + timedelta(days=60)).strftime("%Y-%m-%d")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"},
                params={
                    "reportName": "RPT_FCI_PERFORMANCEE",
                    "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,UPDATE_DATE,REPORT_DATE,NOTICE_DATE,BASIC_EPS,REPORTDATE_TYPE",
                    "pageNumber": 1,
                    "pageSize": 100,
                    "sortTypes": 1,
                    "sortColumns": "NOTICE_DATE",
                    "source": "WEB",
                    "client": "WEB",
                },
            )
            if r.status_code == 200:
                body = r.json()
                data_list = (body.get("result") or {}).get("data") or []
                for item in data_list:
                    update_date = (item.get("UPDATE_DATE") or item.get("NOTICE_DATE") or "")[:10]
                    report_date_raw = (item.get("REPORT_DATE") or "")[:10]
                    if not update_date or update_date < today or update_date > end_date:
                        continue
                    sec_name = item.get("SECURITY_NAME_ABBR", "")
                    sec_code = item.get("SECURITY_CODE", "")
                    report_type = item.get("REPORTDATE_TYPE", "")
                    type_labels = {"1": "一季报", "2": "半年报", "3": "三季报", "4": "年报"}
                    type_label = type_labels.get(str(report_type), "财务报告")
                    title = f"{sec_name} {type_label}披露"
                    if report_date_raw:
                        title += f"（{report_date_raw[:7]}期）"
                    events.append({
                        "date": update_date,
                        "title": title[:80],
                        "description": f"{sec_name}（{sec_code}）将于{update_date}披露{type_label}",
                        "category": "个股公告",
                        "importance": 3 if str(report_type) == "4" else 2,
                        "source_url": f"https://data.eastmoney.com/notices/stock/{sec_code}.html",
                        "source": "eastmoney_earnings",
                        "event_type": EVENT_TYPE_EARNINGS,
                        "country": "CN",
                        "symbol": sec_code,
                    })
        logger.info(f"[事件日历][东方财富财报] 爬取完成: {len(events)} 条, 耗时 {time.time()-t0:.1f}s")
        _record_source_result("eastmoney_earnings", True, len(events), time.time() - t0)
    except Exception as e:
        logger.warning(f"[事件日历][东方财富财报] 爬取失败: {e}")
        _record_source_result("eastmoney_earnings", False, elapsed=time.time() - t0)
    return events


async def fetch_economic_indicators() -> list:
    t0 = time.time()
    events = []
    today = now_bj().strftime("%Y-%m-%d")
    end_date = (now_bj() + timedelta(days=30)).strftime("%Y-%m-%d")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"},
                params={
                    "reportName": "RPT_ECONOMICDATA",
                    "columns": "ALL",
                    "pageNumber": 1,
                    "pageSize": 80,
                    "sortTypes": 1,
                    "sortColumns": "PUBLISH_DATE",
                    "source": "WEB",
                    "client": "WEB",
                },
            )
            if r.status_code == 200:
                body = r.json()
                data_list = (body.get("result") or {}).get("data") or []
                for item in data_list:
                    pub_date = (item.get("PUBLISH_DATE") or "")[:10]
                    if not pub_date or pub_date < today or pub_date > end_date:
                        continue
                    indicator_name = item.get("INDICATOR_NAME", "") or item.get("INDEX_NAME", "")
                    country = item.get("COUNTRY", "CN")
                    previous = item.get("PREVIOUS_VALUE", "")
                    forecast = item.get("FORECAST_VALUE", "")
                    if not indicator_name:
                        continue
                    desc_parts = [indicator_name]
                    if previous:
                        desc_parts.append(f"前值: {previous}")
                    if forecast:
                        desc_parts.append(f"预期: {forecast}")
                    is_cn = country in ("CN", "中国", "")
                    category = "国内热点" if is_cn else "国际热点"
                    importance = 3 if any(kw in indicator_name for kw in ("GDP", "CPI", "PPI", "PMI", "非农", "就业", "利率决议")) else 2
                    events.append({
                        "date": pub_date,
                        "title": f"{indicator_name}公布"[:80],
                        "description": "，".join(desc_parts)[:200],
                        "category": category,
                        "importance": importance,
                        "source_url": "https://data.eastmoney.com/cjsj/hgjjsj.html",
                        "source": "eastmoney_economic",
                        "event_type": EVENT_TYPE_ECONOMIC,
                        "country": "CN" if is_cn else country,
                        "symbol": "",
                    })
        logger.info(f"[事件日历][东方财富经济指标] 爬取完成: {len(events)} 条, 耗时 {time.time()-t0:.1f}s")
        _record_source_result("eastmoney_economic", True, len(events), time.time() - t0)
    except Exception as e:
        logger.warning(f"[事件日历][东方财富经济指标] 爬取失败: {e}")
        _record_source_result("eastmoney_economic", False, elapsed=time.time() - t0)
    return events


async def fetch_corporate_actions() -> list:
    t0 = time.time()
    events = []
    today = now_bj().strftime("%Y-%m-%d")
    end_date = (now_bj() + timedelta(days=60)).strftime("%Y-%m-%d")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"},
                params={
                    "reportName": "RPT_SHAREBONUS_DET",
                    "columns": "ALL",
                    "pageNumber": 1,
                    "pageSize": 80,
                    "sortTypes": 1,
                    "sortColumns": "EX_DIVIDEND_DATE",
                    "source": "WEB",
                    "client": "WEB",
                },
            )
            if r.status_code == 200:
                body = r.json()
                data_list = (body.get("result") or {}).get("data") or []
                for item in data_list:
                    ex_date = (item.get("EX_DIVIDEND_DATE") or "")[:10]
                    if not ex_date or ex_date < today or ex_date > end_date:
                        continue
                    sec_name = item.get("SECURITY_NAME_ABBR", "")
                    sec_code = item.get("SECURITY_CODE", "")
                    bonus_type = item.get("BONUS_TYPE", "")
                    cash = item.get("CASH_BONUS", "")
                    shares = item.get("CONVERTED_SHARES", "")
                    title_parts = [sec_name]
                    if cash:
                        title_parts.append(f"派息{cash}元")
                    if shares:
                        title_parts.append(f"送转{shares}股")
                    if not cash and not shares:
                        title_parts.append("分红派息")
                    title = " ".join(title_parts)
                    desc = f"{sec_name}（{sec_code}）"
                    if cash:
                        desc += f" 每10股派现{cash}元"
                    if shares:
                        desc += f" 每10股送转{shares}股"
                    events.append({
                        "date": ex_date,
                        "title": title[:80],
                        "description": desc[:200],
                        "category": "个股公告",
                        "importance": 3,
                        "source_url": f"https://data.eastmoney.com/notices/stock/{sec_code}.html",
                        "source": "eastmoney_corporate_action",
                        "event_type": EVENT_TYPE_CORPORATE_ACTION,
                        "country": "CN",
                        "symbol": sec_code,
                    })
        logger.info(f"[事件日历][东方财富分红配股] 爬取完成: {len(events)} 条, 耗时 {time.time()-t0:.1f}s")
        _record_source_result("eastmoney_corporate_action", True, len(events), time.time() - t0)
    except Exception as e:
        logger.warning(f"[事件日历][东方财富分红配股] 爬取失败: {e}")
        _record_source_result("eastmoney_corporate_action", False, elapsed=time.time() - t0)
    return events


async def fetch_jin10_calendar_structured() -> list:
    t0 = time.time()
    events = []
    today = now_bj().strftime("%Y-%m-%d")
    end_date = (now_bj() + timedelta(days=15)).strftime("%Y-%m-%d")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(
                "https://cdn.jin10.com/data_center/reports/calendar.json",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.jin10.com/"},
            )
            if r.status_code != 200:
                logger.warning(f"[事件日历][金十日历] 请求失败: {r.status_code}")
                _record_source_result("jin10_calendar", False, elapsed=time.time() - t0)
                return events
            body = r.json()
            for item in (body.get("data") or []):
                date_str = (item.get("date") or item.get("time", ""))[:10]
                if not date_str or date_str < today or date_str > end_date:
                    continue
                title = item.get("title") or item.get("name", "")
                if not title:
                    continue
                content = item.get("content", "") or item.get("description", "")
                country = item.get("country", "CN")
                imp_raw = item.get("importance", "")
                importance = 3 if str(imp_raw).lower() in ("high", "3", "★★★") else (2 if str(imp_raw).lower() in ("medium", "2", "★★") else 1)
                is_cn = country in ("CN", "中国", "")
                category = "国内热点" if is_cn else "国际热点"
                event_type = EVENT_TYPE_GENERAL
                title_lower = title.lower()
                if any(kw in title for kw in ("CPI", "PPI", "PMI", "GDP", "非农", "就业", "失业率", "零售", "工业")):
                    event_type = EVENT_TYPE_ECONOMIC
                elif any(kw in title for kw in ("美联储", "央行", "利率决议", "货币政策", "FOMC", "ECB", "BOJ")):
                    event_type = EVENT_TYPE_CENTRAL_BANK
                elif any(kw in title for kw in ("财报", "业绩", "营收", "盈利")):
                    event_type = EVENT_TYPE_EARNINGS
                elif any(kw in title for kw in ("OPEC", "会议", "论坛", "峰会", "G20", "G7", "达沃斯")):
                    event_type = EVENT_TYPE_CONFERENCE
                events.append({
                    "date": date_str,
                    "title": title[:80],
                    "description": content[:200] if content else "",
                    "category": category,
                    "importance": importance,
                    "source_url": f"https://www.jin10.com/flash_list.html",
                    "source": "jin10_calendar",
                    "event_type": event_type,
                    "country": "CN" if is_cn else country,
                    "symbol": "",
                })
        logger.info(f"[事件日历][金十日历] 结构化解析完成: {len(events)} 条, 耗时 {time.time()-t0:.1f}s")
        _record_source_result("jin10_calendar", True, len(events), time.time() - t0)
    except Exception as e:
        logger.warning(f"[事件日历][金十日历] 结构化解析失败: {e}")
        _record_source_result("jin10_calendar", False, elapsed=time.time() - t0)
    return events


async def fetch_10jqka_calendar() -> list:
    t0 = time.time()
    events = []
    today = now_bj().strftime("%Y-%m-%d")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(
                "https://www.10jqka.com.cn/calendar/",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.10jqka.com.cn/"},
            )
            if r.status_code != 200:
                logger.warning(f"[事件日历][同花顺日历] 请求失败: {r.status_code}")
                _record_source_result("10jqka_calendar", False, elapsed=time.time() - t0)
                return events
            soup = BeautifulSoup(r.text, "lxml")
            cal_items = soup.select(".calendar-item, .event-item, .cal-item, [data-date]")
            if not cal_items:
                cal_items = soup.select("tr[data-date], li[data-date], .item")
            for item in cal_items:
                date_str = ""
                date_attr = item.get("data-date") or item.get("data-time", "")
                if date_attr:
                    date_str = date_attr[:10]
                if not date_str:
                    time_el = item.select_one(".time, .date, .cal-date")
                    if time_el:
                        m = re.search(r"(\d{4}-\d{2}-\d{2})", time_el.get_text())
                        if m:
                            date_str = m.group(1)
                if not date_str or date_str < today:
                    continue
                title_el = item.select_one(".title, .event-title, .cal-title, a")
                title = title_el.get_text(strip=True) if title_el else ""
                if not title or len(title) < 4:
                    continue
                desc_el = item.select_one(".desc, .content, .summary")
                desc = desc_el.get_text(strip=True)[:200] if desc_el else ""
                event_type = EVENT_TYPE_GENERAL
                if any(kw in title for kw in ("央行", "利率", "货币政策", "FOMC", "美联储")):
                    event_type = EVENT_TYPE_CENTRAL_BANK
                elif any(kw in title for kw in ("会议", "论坛", "峰会", "大会")):
                    event_type = EVENT_TYPE_CONFERENCE
                elif any(kw in title for kw in ("监管", "证监会", "银保监", "政策")):
                    event_type = EVENT_TYPE_REGULATORY
                events.append({
                    "date": date_str,
                    "title": title[:80],
                    "description": desc,
                    "category": _classify_timeline_category(title),
                    "importance": 2,
                    "source_url": "https://www.10jqka.com.cn/calendar/",
                    "source": "10jqka_calendar",
                    "event_type": event_type,
                    "country": "CN",
                    "symbol": "",
                })
        logger.info(f"[事件日历][同花顺日历] 爬取完成: {len(events)} 条, 耗时 {time.time()-t0:.1f}s")
        _record_source_result("10jqka_calendar", True, len(events), time.time() - t0)
    except Exception as e:
        logger.warning(f"[事件日历][同花顺日历] 爬取失败: {e}")
        _record_source_result("10jqka_calendar", False, elapsed=time.time() - t0)
    return events


async def _fetch_calendar_sources() -> str:
    """抓取多个财经日历网页源数据"""
    segments = []
    today = now_bj().strftime("%Y-%m-%d")
    end_date = (now_bj() + timedelta(days=15)).strftime("%Y-%m-%d")

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"},
                params={
                    "reportName": "RPT_MACRO_NEWS",
                    "columns": "ALL",
                    "pageNumber": 1,
                    "pageSize": 50,
                    "sortTypes": -1,
                    "sortColumns": "TRADE_DATE",
                    "source": "WEB",
                    "client": "WEB",
                },
            )
            if r.status_code == 200:
                body = r.json()
                data_list = (body.get("result") or {}).get("data") or []
                items = []
                for item in data_list:
                    date_str = (item.get("TRADE_DATE") or "")[:10]
                    title = item.get("TITLE", "")
                    content = item.get("CONTENT", "")
                    url = item.get("URL", "") or item.get("SOURCE_URL", "")
                    if date_str and date_str >= today and date_str <= end_date:
                        items.append(f"[{date_str}] {title} | {content[:80]} | 来源: {url}")
                if items:
                    segments.append("=== 东方财富宏观日历 ===\n" + "\n".join(items[:30]))
    except Exception as e:
        logger.warning(f"东方财富宏观日历抓取失败: {e}")

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(
                "https://np-listapi.eastmoney.com/comm/web/getFastNewsList",
                headers={"User-Agent": "Mozilla/5.0"},
                params={
                    "client": "web",
                    "biz": "web_724",
                    "fastColumn": 101,
                    "pageSize": 30,
                },
            )
            if r.status_code == 200:
                body = r.json()
                items = body.get("list") or body.get("data", {}).get("list") or []
                news_items = []
                for item in items:
                    title = item.get("title") or item.get("art_title", "")
                    date_str = (item.get("show_time") or item.get("date", ""))[:10]
                    url = item.get("url") or item.get("share_url", "")
                    if title and date_str and date_str >= today:
                        news_items.append(f"[{date_str}] {title} | 来源: {url}")
                if news_items:
                    segments.append("=== 东方财富快讯 ===\n" + "\n".join(news_items[:20]))
    except Exception as e:
        logger.warning(f"东方财富快讯抓取失败: {e}")

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(
                "https://data.eastmoney.com/cjsj/hgjjsj.html",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if r.status_code == 200:
                text = r.text[:5000]
                segments.append("=== 东方财富经济数据日历 ===\n" + text)
    except Exception as e:
        logger.warning(f"东方财富经济日历抓取失败: {e}")

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(
                "https://cdn.jin10.com/data_center/reports/calendar.json",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.jin10.com/"},
            )
            if r.status_code == 200:
                body = r.json()
                cal_items = []
                for item in (body.get("data") or []):
                    date_str = (item.get("date") or item.get("time", ""))[:10]
                    title = item.get("title") or item.get("name", "")
                    content = item.get("content", "") or item.get("description", "")
                    country = item.get("country", "")
                    importance = item.get("importance", "")
                    if date_str and date_str >= today and date_str <= end_date and title:
                        cal_items.append(f"[{date_str}] {country} {title} | {content[:60]} | 重要性:{importance}")
                if cal_items:
                    segments.append("=== 金十数据财经日历 ===\n" + "\n".join(cal_items[:30]))
    except Exception as e:
        logger.warning(f"金十财经日历抓取失败: {e}")

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(
                "https://www.jin10.com/flash_newest.js",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.jin10.com/"},
            )
            if r.status_code == 200:
                text = r.text
                text = re.sub(r"^var\s+newest\s*=\s*", "", text)
                text = text.rstrip(";").strip()
                if text:
                    data = json.loads(text)
                    jin10_news = []
                    for item in data[:50]:
                        if str(item.get("type", "")).lower() in ("ad", "advert", "promotion"):
                            continue
                        if item.get("vip"):
                            continue
                        data_content = item.get("data", {})
                        title_raw = (data_content.get("title", "") or data_content.get("content", "")).strip()
                        if not title_raw:
                            continue
                        title_raw = re.sub(r"<[^>]+>", "", title_raw)
                        m = re.match(r"^【([^】]*)】(.*)$", title_raw)
                        title = m.group(1).strip() if m else title_raw
                        time_str = item.get("time", "")
                        if time_str and time_str >= today and title:
                            jin10_news.append(f"[{time_str[:10]}] {title}")
                    if jin10_news:
                        segments.append("=== 金十数据快讯 ===\n" + "\n".join(jin10_news[:20]))
    except Exception as e:
        logger.warning(f"金十快讯抓取失败: {e}")

    current = await fetch_ipo_calendar()
    if current:
        ipo_lines = [
            f"[{e['date']}] {e['title']} | {e['description']} | 来源: {e.get('source_url', '')}"
            for e in current if e.get('date', '') >= today
        ]
        if ipo_lines:
            segments.append("=== 新股日历 ===\n" + "\n".join(ipo_lines[:20]))

    return "\n\n".join(segments)


VALID_CATEGORIES = {"国际热点", "国内热点", "社会热点", "行业热点", "公司热点", "个股公告"}
VALID_EVENT_TYPES = {
    EVENT_TYPE_EARNINGS, EVENT_TYPE_ECONOMIC, EVENT_TYPE_CENTRAL_BANK,
    EVENT_TYPE_CORPORATE_ACTION, EVENT_TYPE_IPO, EVENT_TYPE_REGULATORY,
    EVENT_TYPE_CONFERENCE, EVENT_TYPE_HOLIDAY, EVENT_TYPE_GENERAL,
}
VALID_SOURCES = set(SOURCE_PRIORITY.keys()) | {"crawler", "ai_extracted"}


def _validate_event(ev: dict) -> tuple[bool, list[str]]:
    errors = []
    date_str = ev.get("date", "")
    if not date_str:
        errors.append("missing date")
    else:
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            errors.append(f"invalid date format: {date_str}")
    title = ev.get("title", "")
    if not title or len(title) < 2:
        errors.append("title too short or empty")
    if len(title) > 80:
        errors.append("title exceeds 80 chars")
    category = ev.get("category", "")
    if category and category not in VALID_CATEGORIES:
        errors.append(f"invalid category: {category}")
    importance = ev.get("importance", 0)
    if importance not in (0, 1, 2, 3):
        errors.append(f"invalid importance: {importance}")
    event_type = ev.get("event_type", "")
    if event_type and event_type not in VALID_EVENT_TYPES:
        errors.append(f"invalid event_type: {event_type}")
    source = ev.get("source", "")
    if source and source not in VALID_SOURCES:
        errors.append(f"unknown source: {source}")
    return (len(errors) == 0, errors)


def _normalize_event_title(title: str) -> str:
    return re.sub(r'[\s\u3000\-—_·]+', '', title).lower().strip()


def _title_similarity(t1: str, t2: str) -> float:
    n1 = _normalize_event_title(t1)
    n2 = _normalize_event_title(t2)
    if not n1 or not n2:
        return 0.0
    if n1 == n2:
        return 1.0
    if n1 in n2 or n2 in n1:
        return 0.85
    return fuzzy_search.fuzzy_match_score(n1, n2)


def _cross_verify_events(all_events: list[dict]) -> list[dict]:
    if not all_events:
        return all_events
    date_groups: dict[str, list[dict]] = {}
    for ev in all_events:
        d = ev.get("date", "")
        if d not in date_groups:
            date_groups[d] = []
        date_groups[d].append(ev)
    for date_str, group in date_groups.items():
        for ev in group:
            source_list = [ev.get("source", "")]
            for other in group:
                if other is ev:
                    continue
                sim = _title_similarity(ev.get("title", ""), other.get("title", ""))
                if sim > 0.6:
                    other_source = other.get("source", "")
                    if other_source and other_source not in source_list:
                        source_list.append(other_source)
            ev["data_sources"] = json.dumps(source_list, ensure_ascii=False)
            ev["verified"] = 1 if len(source_list) > 1 else 0
    return all_events


def _resolve_conflicts(all_events: list[dict]) -> list[dict]:
    if not all_events:
        return all_events
    groups: dict[str, list[dict]] = {}
    for ev in all_events:
        key = _normalize_event_title(ev.get("title", ""))[:20]
        if not key:
            continue
        matched = False
        for gk in list(groups.keys()):
            if _title_similarity(ev.get("title", ""), groups[gk][0].get("title", "")) > 0.7:
                groups[gk].append(ev)
                matched = True
                break
        if not matched:
            groups[key] = [ev]
    resolved = []
    for key, group in groups.items():
        if len(group) == 1:
            resolved.append(group[0])
            continue
        group.sort(key=lambda x: SOURCE_PRIORITY.get(x.get("source", ""), 0), reverse=True)
        best = group[0].copy()
        date_counts: dict[str, int] = {}
        for ev in group:
            d = ev.get("date", "")
            date_counts[d] = date_counts.get(d, 0) + 1
        most_common_date = max(date_counts, key=date_counts.get)
        best["date"] = most_common_date
        best["importance"] = max(ev.get("importance", 0) for ev in group)
        all_sources = []
        for ev in group:
            s = ev.get("source", "")
            if s and s not in all_sources:
                all_sources.append(s)
        best["data_sources"] = json.dumps(all_sources, ensure_ascii=False)
        best["verified"] = 1 if len(all_sources) > 1 else 0
        resolved.append(best)
    return resolved


EVENT_CALENDAR_UPDATE_INTERVAL = 15 * 60
EVENT_CALENDAR_FULL_REBUILD_INTERVAL = 24 * 60 * 60
EVENT_CALENDAR_TRADING_INTERVAL = 10 * 60
_EVENT_CALENDAR_LAST_FULL_REBUILD = 0.0


def _insert_event_calendar_cache(events: list):
    if not events:
        return
    now_str = now_bj().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        c = conn.cursor()
        for ev in events:
            event_hash = hashlib.md5(
                f"{ev['date']}|{ev['title'][:40]}|{ev.get('category','')}".encode()
            ).hexdigest()[:16]
            try:
                c.execute(
                    """INSERT OR IGNORE INTO event_calendar_cache
                       (event_date, title, category, event_type, importance, description,
                        source, source_url, country, symbol, verified, data_sources, fetched_at, event_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ev["date"],
                        ev["title"],
                        ev.get("category", "社会热点"),
                        ev.get("event_type", EVENT_TYPE_GENERAL),
                        ev.get("importance", 2),
                        ev.get("description", ""),
                        ev.get("source", ""),
                        ev.get("source_url", ""),
                        ev.get("country", "CN"),
                        ev.get("symbol", ""),
                        ev.get("verified", 0),
                        ev.get("data_sources", ""),
                        now_str,
                        event_hash,
                    ),
                )
            except Exception as e:
                logger.warning(f"插入日历缓存事件失败: {e}")
        conn.commit()


def _load_event_calendar_from_db() -> list:
    events = []
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                """SELECT id, event_date, title, category, event_type, importance, description,
                          source, source_url, country, symbol, verified, data_sources
                   FROM event_calendar_cache
                   WHERE event_date >= date('now','localtime')
                   ORDER BY event_date ASC, importance DESC, id ASC
                   LIMIT 120"""
            )
            for row in c.fetchall():
                events.append({
                    "id": row["id"],
                    "date": row["event_date"],
                    "title": row["title"],
                    "category": row["category"],
                    "event_type": row["event_type"] if "event_type" in row.keys() else EVENT_TYPE_GENERAL,
                    "importance": row["importance"],
                    "description": row["description"],
                    "source": row["source"],
                    "source_url": row["source_url"],
                    "country": row["country"] if "country" in row.keys() else "CN",
                    "symbol": row["symbol"] if "symbol" in row.keys() else "",
                    "verified": row["verified"] if "verified" in row.keys() else 0,
                    "data_sources": row["data_sources"] if "data_sources" in row.keys() else "",
                })
    except Exception as e:
        logger.warning(f"[事件日历] 从DB加载缓存失败: {e}")
    return events


async def _build_event_calendar(full_rebuild: bool = False):
    global _event_calendar_in_progress, _EVENT_CALENDAR_LAST_FULL_REBUILD
    if _event_calendar_in_progress:
        return
    _event_calendar_in_progress = True
    t0 = time.time()
    try:
        if full_rebuild:
            with get_db() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM event_calendar_cache")
                conn.commit()
            _EVENT_CALENDAR_LAST_FULL_REBUILD = time.time()
            logger.info("[事件日历] 开始全量重建...")

        fetch_tasks = []
        source_names = [
            ("eastmoney_earnings", fetch_earnings_calendar),
            ("yiqiliu_calendar", fetch_yiqiliu_calendar_events),
            ("postproxy_calendar", fetch_postproxy_calendar_events),
            ("chinese_holiday", fetch_chinese_holidays_and_trading_calendar),
            ("ipo_calendar", fetch_ipo_calendar),
            ("sina_announcement", fetch_sina_announcements),
        ]
        for name, fn in source_names:
            if _is_source_available(name):
                fetch_tasks.append(fn())
            else:
                logger.info(f"[事件日历][{name}] 数据源降级中，跳过本轮")

        results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        all_events = []
        for result in results:
            if isinstance(result, list):
                all_events.extend(result)
            elif isinstance(result, Exception):
                logger.warning(f"[事件日历] 数据源并发获取异常: {result}")

        validated_events = []
        validation_errors = 0
        for ev in all_events:
            is_valid, errors = _validate_event(ev)
            if is_valid:
                validated_events.append(ev)
            else:
                validation_errors += 1
                logger.debug(f"[事件日历] 事件验证失败: {ev.get('title', '')[:30]} - {errors}")
        if validation_errors:
            logger.info(f"[事件日历] 验证过滤: {validation_errors} 条无效事件")

        verified_events = _cross_verify_events(validated_events)
        resolved_events = _resolve_conflicts(verified_events)

        for ev in resolved_events:
            if "event_type" not in ev:
                ev["event_type"] = EVENT_TYPE_GENERAL
            if "country" not in ev:
                ev["country"] = "CN"
            if "symbol" not in ev:
                ev["symbol"] = ""
            if "verified" not in ev:
                ev["verified"] = 0
            if "data_sources" not in ev:
                ev["data_sources"] = json.dumps([ev.get("source", "")], ensure_ascii=False)

        _insert_event_calendar_cache(resolved_events)

        if len(resolved_events) < 20:
            raw = await _fetch_calendar_sources()
            if raw:
                try:
                    system_prompt = """你是一位财经数据专家。请分析以下抓取的财经日历原始数据，提取出未来15天的重要事件。

对每个事件，请提供：
1. date: 事件日期 (YYYY-MM-DD)
2. title: 事件标题 (10-30字)
3. description: 事件描述 (20-50字)
4. category: 分类，从以下选择: ["国际热点", "国内热点", "社会热点", "行业热点", "公司热点", "个股公告"]
5. importance: 重要性 (1-3，3为最高)
6. source_url: 源链接URL（如果有），没有则留空字符串

请严格按照JSON格式返回，不要包含其他文字：
{"events": [{"date": "2026-05-27", "title": "...", "description": "...", "category": "...", "importance": 2, "source_url": "..."}, ...]}"""

                    user_prompt = f"以下是抓取的财经日历数据，请提取结构化事件：\n\n{raw}"

                    content = await nvidia_client.call_nvidia(
                        [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.1,
                        max_tokens=4096,
                    )

                    content = content.strip()
                    if content.startswith("```"):
                        content = content.split("\n", 1)[-1]
                        content = content.rsplit("```", 1)[0]
                    content = content.strip()

                    parsed = json.loads(content)
                    ai_events = parsed.get("events", [])
                    if isinstance(ai_events, list) and ai_events:
                        for ev in ai_events:
                            if not ev.get("source_url"):
                                ev["source_url"] = f"https://so.eastmoney.com/news/s?keyword={quote(ev.get('title', ''))}"
                            ev["source"] = "ai_extracted"
                            ev["event_type"] = EVENT_TYPE_GENERAL
                            ev["country"] = "CN"
                            ev["symbol"] = ""
                            ev["verified"] = 0
                            ev["data_sources"] = json.dumps(["ai_extracted"], ensure_ascii=False)
                        _insert_event_calendar_cache(ai_events)
                        logger.info(f"[事件日历] AI补充提取: {len(ai_events)} 条事件")
                except json.JSONDecodeError as e:
                    logger.error(f"[事件日历] AI返回JSON解析失败: {e}")
                except Exception as e:
                    logger.error(f"[事件日历] AI提取异常: {e}")

        db_events = _load_event_calendar_from_db()
        _EVENT_CALENDAR_CACHE["data"] = db_events
        _EVENT_CALENDAR_CACHE["updated_at"] = now_bj().strftime("%Y-%m-%d %H:%M:%S")

        elapsed = time.time() - t0
        logger.info(
            f"[事件日历] 构建完成: 采集{len(resolved_events)}条, "
            f"DB缓存{len(db_events)}条, 耗时{elapsed:.1f}s"
        )

    except Exception as e:
        logger.error(f"[事件日历] 构建异常: {e}")
    finally:
        _event_calendar_in_progress = False


async def _event_calendar_update_loop():
    last_update = time.time()
    last_full_rebuild = time.time()
    while True:
        try:
            now = time.time()
            bj = now_bj()
            in_trading = is_trading_hours()
            interval = EVENT_CALENDAR_TRADING_INTERVAL if in_trading else EVENT_CALENDAR_UPDATE_INTERVAL
            if now - last_update >= interval:
                full_rebuild = (now - last_full_rebuild) >= EVENT_CALENDAR_FULL_REBUILD_INTERVAL
                await _build_event_calendar(full_rebuild=full_rebuild)
                last_update = now
                if full_rebuild:
                    last_full_rebuild = now
        except Exception as e:
            logger.error(f"[事件日历] 更新循环异常: {e}")
        await asyncio.sleep(60)


async def _event_calendar_startup_build():
    await asyncio.sleep(20)
    if not _EVENT_CALENDAR_CACHE["data"]:
        db_events = _load_event_calendar_from_db()
        if db_events:
            _EVENT_CALENDAR_CACHE["data"] = db_events
            _EVENT_CALENDAR_CACHE["updated_at"] = now_bj().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"[事件日历] 从DB缓存加载: {len(db_events)} 条")
        else:
            await _build_event_calendar(full_rebuild=True)


@app.get("/api/events")
async def get_events(
    event_type: str = Query(None),
    importance: int = Query(None),
    keyword: str = Query(None),
):
    data = _EVENT_CALENDAR_CACHE["data"]
    if not data:
        data = []
    if event_type:
        types = [t.strip() for t in event_type.split(",")]
        data = [e for e in data if e.get("event_type", EVENT_TYPE_GENERAL) in types]
    if importance is not None:
        data = [e for e in data if e.get("importance", 0) >= importance]
    if keyword:
        kw = keyword.strip().lower()
        data = [e for e in data if kw in (e.get("title", "") + e.get("description", "")).lower()]
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "data": data,
            "updated_at": _EVENT_CALENDAR_CACHE["updated_at"],
            "event_types": list(EVENT_TYPE_LABELS.keys()),
        },
    )


@app.get("/api/events/stats")
async def get_events_stats():
    source_health = {}
    for name, health in _SOURCE_HEALTH.items():
        source_health[name] = {
            "consecutive_failures": health["consecutive_failures"],
            "last_success": health["last_success"],
            "last_failure": health["last_failure"],
            "last_count": health["last_count"],
            "last_elapsed": health["last_elapsed"],
            "degraded": health["degraded"],
        }
    type_stats = {}
    category_stats = {}
    verified_count = 0
    total = 0
    for ev in _EVENT_CALENDAR_CACHE["data"]:
        total += 1
        et = ev.get("event_type", EVENT_TYPE_GENERAL)
        type_stats[et] = type_stats.get(et, 0) + 1
        cat = ev.get("category", "")
        category_stats[cat] = category_stats.get(cat, 0) + 1
        if ev.get("verified", 0) > 0:
            verified_count += 1
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "total_events": total,
            "verified_events": verified_count,
            "type_stats": type_stats,
            "category_stats": category_stats,
            "source_health": source_health,
            "updated_at": _EVENT_CALENDAR_CACHE["updated_at"],
            "event_type_labels": EVENT_TYPE_LABELS,
        },
    )


@app.put("/api/events/{event_id}/verify")
async def verify_event(event_id: int, verified: int = Query(...), title: str = Query(None), event_date: str = Query(None), category: str = Query(None), importance: int = Query(None), description: str = Query(None)):
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM event_calendar_cache WHERE id = ?", (event_id,))
            if not c.fetchone():
                return JSONResponse(status_code=404, content={"success": False, "error": "事件不存在"})
            updates = ["verified = ?"]
            params = [verified]
            if title is not None:
                updates.append("title = ?")
                params.append(title[:80])
            if event_date is not None:
                updates.append("event_date = ?")
                params.append(event_date)
            if category is not None and category in VALID_CATEGORIES:
                updates.append("category = ?")
                params.append(category)
            if importance is not None and importance in (1, 2, 3):
                updates.append("importance = ?")
                params.append(importance)
            if description is not None:
                updates.append("description = ?")
                params.append(description[:200])
            params.append(event_id)
            c.execute(f"UPDATE event_calendar_cache SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
        db_events = _load_event_calendar_from_db()
        _EVENT_CALENDAR_CACHE["data"] = db_events
        _EVENT_CALENDAR_CACHE["updated_at"] = now_bj().strftime("%Y-%m-%d %H:%M:%S")
        return JSONResponse(status_code=200, content={"success": True})
    except Exception as e:
        logger.error(f"[事件日历] 审核更新失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.post("/api/events")
async def add_event(
    event_date: str = Query(...),
    title: str = Query(...),
    category: str = Query("社会热点"),
    importance: int = Query(2),
    description: str = Query(""),
    event_type: str = Query(EVENT_TYPE_GENERAL),
    source_url: str = Query(""),
    country: str = Query("CN"),
    symbol: str = Query(""),
):
    if not event_date or not title:
        return JSONResponse(status_code=400, content={"success": False, "error": "日期和标题为必填项"})
    try:
        datetime.strptime(event_date, "%Y-%m-%d")
    except ValueError:
        return JSONResponse(status_code=400, content={"success": False, "error": "日期格式无效，需YYYY-MM-DD"})
    ev = {
        "date": event_date,
        "title": title[:80],
        "category": category if category in VALID_CATEGORIES else "社会热点",
        "importance": importance if importance in (1, 2, 3) else 2,
        "description": description[:200],
        "source_url": source_url,
        "source": "manual",
        "event_type": event_type if event_type in VALID_EVENT_TYPES else EVENT_TYPE_GENERAL,
        "country": country,
        "symbol": symbol,
        "verified": 2,
        "data_sources": json.dumps(["manual"], ensure_ascii=False),
    }
    is_valid, errors = _validate_event(ev)
    if not is_valid:
        return JSONResponse(status_code=400, content={"success": False, "error": f"验证失败: {errors}"})
    try:
        _insert_event_calendar_cache([ev])
        db_events = _load_event_calendar_from_db()
        _EVENT_CALENDAR_CACHE["data"] = db_events
        _EVENT_CALENDAR_CACHE["updated_at"] = now_bj().strftime("%Y-%m-%d %H:%M:%S")
        return JSONResponse(status_code=200, content={"success": True})
    except Exception as e:
        logger.error(f"[事件日历] 手动添加事件失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


TIMELINE_CATEGORIES = ["国际热点", "国内热点", "社会热点", "行业热点", "公司热点", "个股公告"]


@app.get("/api/timeline")
async def get_timeline(category: str = Query(None)):
    data = _TIMELINE_DATA_CACHE["data"]
    if not data:
        data = []
    if category:
        cats = [c.strip() for c in category.split(",")]
        data = [e for e in data if e["category"] in cats]
    stats = {"total": len(data), "filtered": len(data)}
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
    except Exception as e:
        logger.warning(f"WebSocket 异常: {e}")
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

    ssl_keyfile = os.environ.get("SSL_KEYFILE")
    ssl_certfile = os.environ.get("SSL_CERTFILE")
    ssl_kwargs = {}
    if ssl_keyfile and ssl_certfile:
        ssl_kwargs = {"ssl_keyfile": ssl_keyfile, "ssl_certfile": ssl_certfile}

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        reload=False,
        workers=1,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
        **ssl_kwargs,
    )
