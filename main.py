import ssl
import os
import io
import re
import time
import json
import html
import asyncio
import sqlite3
import logging
import tracemalloc
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from contextlib import asynccontextmanager

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

tracemalloc.start()

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


# GDELT 需要忽略 SSL 验证
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(_background_fetch_loop())
    yield


app = FastAPI(
    title="财经新闻实时展示", docs_url=None, redoc_url=None, lifespan=lifespan
)
app.mount("/static", StaticFiles(directory="static"), name="static")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news.db")
MAX_DB_SIZE_MB = 500  # 数据库最大 500MB


# ========== SQLite ==========
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_publish_ts ON news(publish_ts DESC, id DESC)"
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_created ON news(created_at ASC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_title ON news(title)")
        conn.commit()
        yield conn
    finally:
        conn.close()


def db_insert_news(news_list):
    if not news_list:
        return [], 0
    with get_db() as conn:
        c = conn.cursor()
        new_hashes = []
        inserted = 0
        for n in news_list:
            title_hash = f"{n['title'][:30]}|{n['source']}"
            try:
                c.execute(
                    """
                    INSERT OR IGNORE INTO news (title, url, source, publish_time, publish_ts, intro, title_hash, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        n["title"],
                        n["url"],
                        n["source"],
                        n["publish_time"],
                        n.get("publish_ts", 0),
                        n["intro"],
                        title_hash,
                        now_bj().strftime("%Y-%m-%d %H:%M:%S"),
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
            SELECT title, url, source, publish_time, publish_ts, intro 
            FROM news 
            WHERE instr(lower(title), lower(?)) OR instr(lower(intro), lower(?)) OR instr(lower(source), lower(?))
            ORDER BY publish_ts DESC, id DESC
            LIMIT ? OFFSET ?
        """,
            (query, query, query, limit, offset),
        )
        rows = [dict(row) for row in c.fetchall()]

        highlight_pattern = re.compile(re.escape(query), re.IGNORECASE)
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


def db_get_news(limit=10, offset=0):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT title, url, source, publish_time, publish_ts, intro FROM news ORDER BY publish_ts DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = [dict(row) for row in c.fetchall()]
    return rows


def db_count():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM news")
        count = c.fetchone()[0]
    return count


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
        query += " ORDER BY publish_ts DESC, id DESC"
        c.execute(query, params)
        rows = [dict(row) for row in c.fetchall()]
    return rows


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


# ========== 时间戳 & 源配置 ==========
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
    "新浪财经": "#0891B2",
    "财联社": "#E11D48",
    "同花顺": "#F59E0B",
    "东方财富": "#FF6600",
    "GDELT": "#059669",
    "雅虎财经": "#00B4D8",
    "Google News": "#8B5CF6",
    "21经济网": "#DC2626",
    "华尔街见闻": "#7C3AED",
    "雪球": "#0EA5E9",
    "金十数据": "#F97316",
    "格隆汇": "#10B981",
    "法布财经": "#EC4899",
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
            "maxrecords": 100,
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
        "url": "https://www.21jingji.com/",
        "headers": {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.21jingji.com/",
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


# ========== 抓取 ==========

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
        # GDELT 有速率限制，需要延迟请求
        if source_name == "GDELT":
            await asyncio.sleep(5)
        
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            verify=False if source_name == "GDELT" else True,
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

            # 判断 GET 还是 POST
            if method == "POST":
                response = await client.post(**kwargs)
            else:
                response = await client.get(**kwargs)

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
                        desc_soup = BeautifulSoup(desc_html, "html.parser")
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

            # 21经济网 - 尝试通过 AJAX API 获取
            elif source_name == "21经济网":
                soup = BeautifulSoup(response.text, "html.parser")
                html_text = soup.get_text(separator=" ", strip=True)
                kuaixun_pattern = (
                    r"(\d{2}):(\d{2})\s*([^\n]{5,100}?)\s*(南财智讯[^\n]{30,400})"
                )
                matches = re.findall(kuaixun_pattern, html_text[:30000])
                for hour, minute, title, content in matches:
                    title = title.strip().rstrip("：:").strip()
                    if not title or len(title) < 4:
                        continue
                    now = now_bj()
                    try:
                        dt = now.replace(hour=int(hour), minute=int(minute), second=0)
                        if dt > now:
                            dt = dt - timedelta(days=1)
                        pt = dt.strftime("%Y-%m-%d %H:%M:%S")
                        ts = int(dt.replace(tzinfo=TZ_BJ).timestamp())
                    except (ValueError, TypeError):
                        continue
                    if ts <= last_ts:
                        continue
                    content = re.sub(r"\s+", " ", content).strip()
                    content_core = re.sub(
                        r"南财智讯\d{1,2}月\d{1,2}日电[，,]", "", content
                    )
                    news_list.append(
                        {
                            "title": title[:80],
                            "url": "#",
                            "source": source_name,
                            "publish_time": pt,
                            "publish_ts": ts,
                            "intro": content_core[:150]
                            if content_core
                            else content[:150],
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
                articles = soup.select(".timeline__item, .status-item, [class*='timeline'] li, [class*='status'] li")
                if not articles:
                    articles = soup.find_all("li")
                
                for article in articles:
                    content_elem = article.select_one(".content, [class*='content'], p")
                    time_elem = article.select_one(".time, [class*='time'], [class*='date']")
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
                                dt = datetime.strptime(time_text[:19], "%Y-%m-%d %H:%M:%S")
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
                        if 5 in (item.get("channel") or []):
                            continue
                        data_content = item.get("data", {})
                        title_raw = (
                            data_content.get("title", "")
                            or data_content.get("content", "")
                        ).strip()
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
                            dt = datetime.strptime(pub_clean, "%a, %d %b %Y %H:%M:%S %z")
                            ts = int(dt.timestamp())
                            pt = bj_str_from_ts(ts)
                    except (ValueError, TypeError):
                        pass

                    if ts <= last_ts:
                        continue

                    intro = ""
                    if desc_tag and desc_tag.text:
                        desc_soup = BeautifulSoup(desc_tag.text, "html.parser")
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
        except Exception as e:
            logger.error(f"后台抓取异常: {e}")
        await asyncio.sleep(FETCH_INTERVAL)


# ========== 路由 ==========
@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse("static/favicon.png", media_type="image/png")


@app.get("/api/news")
async def get_news_api(
    page: int = Query(1, ge=1), page_size: int = Query(10, ge=5, le=50)
):
    try:
        total = db_count()
        offset = (page - 1) * page_size
        all_news = db_get_news(limit=page_size, offset=offset)

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
                "source_stats": last_fetch_result["source_stats"],
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
    news = db_get_all_for_export(start_date, end_date)
    data = json.dumps(news, ensure_ascii=False, indent=2)
    fn = f"news_{start_date or 'all'}_{end_date or 'all'}.json"
    return StreamingResponse(
        io.BytesIO(data.encode("utf-8")),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={fn}"},
    )


@app.get("/api/export/check")
async def export_check(start_date: str = Query(None), end_date: str = Query(None)):
    """验证接口：只返回新闻数量和基本信息，不返回完整数据"""
    news = db_get_all_for_export(start_date, end_date)
    return {
        "success": True,
        "count": len(news),
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
    news = db_get_all_for_export(start_date, end_date)
    date_range = f"{start_date or '最早'} ~ {end_date or '最新'}"
    rows_parts = []
    for n in news:
        color = SOURCE_COLORS.get(n["source"], "#3498db")
        rows_parts.append(f"""<tr>
<td style="padding:8px;border:1px solid #ddd;">{html.escape(n["title"])}</td>
<td style="padding:8px;border:1px solid #ddd;"><span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;">{html.escape(n["source"])}</span></td>
<td style="padding:8px;border:1px solid #ddd;">{html.escape(n["publish_time"])}</td>
<td style="padding:8px;border:1px solid #ddd;">{html.escape(n["intro"][:80])}</td>
</tr>""")
    rows = "\n".join(rows_parts)
    html_content = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>财经新闻导出</title>
<style>body{{font-family:sans-serif;margin:20px;background:#f5f5f5;}}table{{border-collapse:collapse;background:#fff;width:100%;}}th{{background:#2c3e50;color:#fff;padding:10px;text-align:left;}}tr:nth-child(even){{background:#f9f9f9;}}</style></head>
<body><h2>财经新闻导出 - {now_bj().strftime("%Y-%m-%d %H:%M:%S")}</h2>
<p>时间范围：{html.escape(date_range)} | 共 {len(news)} 条新闻</p>
<table><tr><th>标题</th><th>来源</th><th>时间</th><th>摘要</th></tr>{rows}</table></body></html>"""
    fn = f"news_{start_date or 'all'}_{end_date or 'all'}.html"
    return StreamingResponse(
        io.BytesIO(html_content.encode("utf-8")),
        media_type="text/html",
        headers={"Content-Disposition": f"attachment; filename={fn}"},
    )


@app.get("/api/health")
async def health_check():
    current, _ = tracemalloc.get_traced_memory()
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


if __name__ == "__main__":
    db_cleanup_if_needed()
    db_backfill_publish_ts()

    import asyncio
    import sys

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    port = int(os.environ.get("PORT", 10842))
    import uvicorn

    uvicorn.run(
        app, host="0.0.0.0", port=port, reload=False, workers=1, log_level="info"
    )
