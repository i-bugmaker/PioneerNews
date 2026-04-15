"""
PioneerNews - Cloudflare Workers Python 版本
财经新闻实时播报系统
"""

import json
import time
import re
import asyncio
from datetime import datetime
from urllib.parse import urlparse, parse_qs

# Cloudflare Workers Python SDK
from workers import WorkerEntrypoint, Response
from workers.db import D1Database


# ========== 全局状态 ==========
# 使用 D1 后不需要在内存中保存，但保留用于增量抓取
source_last_ts = {
    "新浪财经": 0,
    "财联社": 0,
    "同花顺": 0,
    "东方财富": 0,
    "GDELT": 0,
    "雅虎财经": 0,
    "Google News": 0,
}

SOURCE_COLORS = {
    "新浪财经": "#0891B2",
    "财联社": "#E11D48",
    "同花顺": "#F59E0B",
    "东方财富": "#FF6600",
    "GDELT": "#6366F1",
    "雅虎财经": "#00B4D8",
    "Google News": "#8B5CF6",
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
        "url": "https://query1.finance.yahoo.com/v1/finance/search",
        "headers": {"User-Agent": "Mozilla/5.0"},
        "params": {"q": "finance", "quotesCount": 10, "newsCount": 20},
    },
    {
        "name": "Google News",
        "url": "https://news.google.com/rss?topic=b&hl=en-US&gl=US&ceid=US:en",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        },
    },
]


# ========== 数据库操作 ==========
class DB:
    def __init__(self, d1: D1Database):
        self.d1 = d1

    async def init_schema(self):
        """初始化数据库表"""
        await self.d1.exec("""
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT,
                source TEXT NOT NULL,
                publish_time TEXT,
                intro TEXT,
                title_hash TEXT UNIQUE,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        await self.d1.exec(
            "CREATE INDEX IF NOT EXISTS idx_publish_time ON news(publish_time DESC)"
        )
        await self.d1.exec(
            "CREATE INDEX IF NOT EXISTS idx_created_at ON news(created_at ASC)"
        )

    async def insert_news(self, news_list: list) -> tuple:
        """插入新闻，返回 (new_hashes, inserted_count)"""
        if not news_list:
            return [], 0

        new_hashes = []
        inserted = 0

        for n in news_list:
            title_hash = f"{n['title'][:30]}|{n['source']}"
            try:
                result = await self.d1.exec(
                    """
                    INSERT OR IGNORE INTO news (title, url, source, publish_time, intro, title_hash, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        n["title"],
                        n["url"],
                        n["source"],
                        n["publish_time"],
                        n["intro"],
                        title_hash,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )

                # D1 返回的结果检查
                if result and hasattr(result, "success") and result.success:
                    # 尝试获取最后插入的 ID 来判断是否真正插入
                    check = await self.d1.exec("SELECT changes() as cnt")
                    if check and len(check) > 0:
                        # 如果成功插入
                        new_hashes.append(title_hash)
                        inserted += 1
            except Exception as e:
                pass

        return new_hashes, inserted

    async def get_news(self, limit: int = 10, offset: int = 0) -> list:
        """获取新闻列表"""
        result = await self.d1.exec(
            """
            SELECT title, url, source, publish_time, intro 
            FROM news 
            ORDER BY publish_time DESC 
            LIMIT ? OFFSET ?
        """,
            (limit, offset),
        )

        if not result or len(result) == 0:
            return []

        # 解析结果 - D1 返回的是列式数据
        rows = []
        if hasattr(result, "results"):
            for row in result.results:
                rows.append(
                    {
                        "title": row.get("title"),
                        "url": row.get("url"),
                        "source": row.get("source"),
                        "publish_time": row.get("publish_time"),
                        "intro": row.get("intro"),
                    }
                )
        return rows

    async def count(self) -> int:
        """获取新闻总数"""
        result = await self.d1.exec("SELECT COUNT(*) as cnt FROM news")
        if result and len(result) > 0 and hasattr(result[0], "results"):
            return result[0].results[0].get("cnt", 0) if result[0].results else 0
        return 0

    async def get_all_for_export(
        self, start_date: str = None, end_date: str = None
    ) -> list:
        """获取导出的新闻"""
        query = "SELECT title, url, source, publish_time, intro FROM news WHERE 1=1"
        params = []

        if start_date:
            query += " AND publish_time >= ?"
            params.append(start_date)
        if end_date:
            query += " AND publish_time <= ?"
            params.append(end_date + " 23:59:59")

        query += " ORDER BY publish_time DESC"

        result = await self.d1.exec(query, params)

        if not result or len(result) == 0:
            return []

        rows = []
        if hasattr(result, "results"):
            for row in result.results:
                rows.append(
                    {
                        "title": row.get("title"),
                        "url": row.get("url"),
                        "source": row.get("source"),
                        "publish_time": row.get("publish_time"),
                        "intro": row.get("intro"),
                    }
                )
        return rows

    async def get_dates(self) -> list:
        """获取所有日期"""
        result = await self.d1.exec("""
            SELECT DISTINCT substr(publish_time, 1, 10) as d 
            FROM news 
            ORDER BY d DESC
        """)

        if not result or len(result) == 0:
            return []

        dates = []
        if hasattr(result, "results"):
            for row in result.results:
                dates.append(row.get("d"))
        return dates

    async def reset(self):
        """清空新闻表"""
        await self.d1.exec("DELETE FROM news")


# ========== 新闻抓取 ==========
async def fetch_news_from_source(source: dict) -> list:
    """从单个新闻源抓取新闻"""
    news_list = []
    source_name = source["name"]
    last_ts = source_last_ts.get(source_name, 0)

    try:
        # 构建请求 URL
        url = source["url"]
        headers = source.get("headers", {})
        params = source.get("params", {})

        # 添加时间戳防止缓存
        if params:
            params["req_trace"] = str(int(time.time() * 1000))

        # 使用 fetch API (Workers Python 内置)
        import urllib.parse

        full_url = url
        if params:
            query_string = urllib.parse.urlencode(params)
            full_url = f"{url}{'&' if '?' in url else '?'}{query_string}"

        # 简单的 HTTP 请求
        import urllib.request

        req = urllib.request.Request(full_url, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read().decode("utf-8")
        except Exception as e:
            print(f"请求失败: {source_name} - {e}")
            return news_list

        # 解析响应
        if source_name == "Google News":
            # RSS XML 解析
            try:
                from html import unescape
                import re

                # 简单 XML 解析
                items = re.findall(r"<item>(.*?)</item>", content, re.DOTALL)
                for item in items:
                    title_match = re.search(r"<title>(.*?)</title>", item)
                    source_match = re.search(r"<source>(.*?)</source>", item)
                    pubdate_match = re.search(r"<pubDate>(.*?)</pubDate>", item)
                    link_match = re.search(r"<link>(.*?)</link>", item)
                    desc_match = re.search(r"<description>(.*?)</description>", item)

                    if not title_match:
                        continue

                    full_title = unescape(title_match.group(1))
                    parts = full_title.rsplit(" - ", 1)
                    if len(parts) == 2:
                        clean_title, source_from_title = parts
                    else:
                        clean_title = full_title
                        source_from_title = ""

                    source_from_tag = (
                        source_match.group(1) if source_match else source_from_title
                    )

                    # 解析日期
                    pub_date = pubdate_match.group(1) if pubdate_match else ""
                    try:
                        from email.utils import parsedate_to_datetime

                        dt = parsedate_to_datetime(pub_date)
                        pt = dt.strftime("%Y-%m-%d %H:%M:%S")
                        ts = int(dt.timestamp())
                    except:
                        pt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        ts = 0

                    if ts <= last_ts:
                        continue

                    link = link_match.group(1) if link_match else "#"
                    desc = desc_match.group(1) if desc_match else ""
                    intro = desc[:150] if desc else ""

                    news_list.append(
                        {
                            "title": clean_title.strip() or "无标题",
                            "url": link,
                            "source": source_name,
                            "publish_time": pt,
                            "intro": f"[{source_from_tag}] {intro}"
                            if source_from_tag
                            else intro,
                        }
                    )
            except Exception as e:
                print(f"Google News 解析错误: {e}")
        else:
            # JSON 解析
            try:
                data = json.loads(content)
            except:
                data = {}

            if source_name == "新浪财经":
                for a in data.get("result", {}).get("data", []):
                    ctime = a.get("ctime", "")
                    ts = int(ctime) if ctime and str(ctime).isdigit() else 0
                    if ts <= last_ts:
                        continue
                    pt = (
                        datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                        if ts
                        else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    )
                    news_list.append(
                        {
                            "title": (a.get("title") or "无标题").strip(),
                            "url": a.get("url", "#"),
                            "source": source_name,
                            "publish_time": pt,
                            "intro": (a.get("intro", "") or "")[:150],
                        }
                    )

            elif source_name == "财联社":
                for a in data.get("data", {}).get("roll_data", []):
                    ctime = a.get("ctime", "")
                    ts = int(ctime) if ctime and str(ctime).isdigit() else 0
                    if ts <= last_ts:
                        continue
                    pt = (
                        datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                        if ts
                        else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    )
                    title = (a.get("title") or a.get("brief", "") or "无标题").strip()[
                        :50
                    ]
                    news_list.append(
                        {
                            "title": title or "无标题",
                            "url": f"https://www.cls.cn/detail/{a.get('id', '')}"
                            if a.get("id")
                            else (a.get("shareurl", "#")),
                            "source": source_name,
                            "publish_time": pt,
                            "intro": (a.get("brief", "") or a.get("content", "") or "")[
                                :150
                            ],
                        }
                    )

            elif source_name == "同花顺":
                for a in data.get("data", {}).get("list", []):
                    ctime = a.get("ctime", "")
                    ts = int(ctime) if ctime and str(ctime).isdigit() else 0
                    if ts <= last_ts:
                        continue
                    pt = (
                        datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                        if ts
                        else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    )
                    share_url = a.get("shareUrl", "")
                    url = "#"
                    if share_url and "/share/" in share_url:
                        m = re.search(r"/share/(\d+)/?", share_url)
                        if m:
                            aid = m.group(1)
                            date_str = (
                                datetime.fromtimestamp(ts).strftime("%Y%m%d")
                                if ts
                                else "unknown"
                            )
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
                            "intro": (a.get("digest", "") or a.get("short", "") or "")[
                                :150
                            ],
                        }
                    )

            elif source_name == "东方财富":
                for a in data.get("data", {}).get("fastNewsList", []):
                    st = a.get("showTime", "")
                    try:
                        dt = datetime.strptime(st[:19], "%Y-%m-%d %H:%M:%S")
                        ts = int(dt.timestamp())
                    except:
                        ts = 0
                    if ts <= last_ts:
                        continue
                    pt = st[:19] if st else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    code = a.get("code", "")
                    news_list.append(
                        {
                            "title": (a.get("title") or "无标题").strip(),
                            "url": f"https://finance.eastmoney.com/a/{code}.html"
                            if code
                            else "#",
                            "source": source_name,
                            "publish_time": pt,
                            "intro": (a.get("summary", "") or "")[:150],
                        }
                    )

            elif source_name == "GDELT":
                items = data.get("articles", [])
                for a in items:
                    st = a.get("seendate", "")
                    ts = 0
                    try:
                        dt = datetime.strptime(st[:15], "%Y%m%dT%H%M%S")
                        ts = int(dt.timestamp())
                    except:
                        pass
                    if ts <= last_ts:
                        continue
                    pt = (
                        f"{st[:4]}-{st[4:6]}-{st[6:8]} {st[9:11]}:{st[11:13]}:{st[13:15]}"
                        if len(st) >= 15
                        else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    )
                    news_list.append(
                        {
                            "title": (a.get("title", "") or "无标题").strip(),
                            "url": a.get("url", "#"),
                            "source": source_name,
                            "publish_time": pt,
                            "intro": (
                                a.get("sourceurl", "") or a.get("domain", "") or ""
                            )[:150],
                        }
                    )

            elif source_name == "雅虎财经":
                items = data.get("news", [])
                for a in items:
                    pub = a.get("publisher", "")
                    pub_time = a.get("providerPublishTime", 0)
                    ts = 0
                    try:
                        if pub_time and isinstance(pub_time, (int, float)):
                            ts = int(pub_time)
                    except:
                        pass
                    if ts <= last_ts:
                        continue
                    pt = (
                        datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                        if ts
                        else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    )
                    link = a.get("link", "") or a.get("url", "#")
                    news_list.append(
                        {
                            "title": (a.get("title", "") or "无标题").strip(),
                            "url": link,
                            "source": source_name,
                            "publish_time": pt,
                            "intro": f"[{pub}]" if pub else "",
                        }
                    )

    except Exception as e:
        print(f"获取{source_name}失败：{str(e)}")

    # 更新最后时间戳
    if news_list:
        timestamps = []
        for n in news_list:
            try:
                dt = datetime.strptime(n["publish_time"], "%Y-%m-%d %H:%M:%S")
                timestamps.append(int(dt.timestamp()))
            except:
                pass
        if timestamps:
            source_last_ts[source_name] = max(timestamps)

    return news_list


async def fetch_new_news() -> tuple:
    """并发抓取所有新闻源"""
    tasks = [fetch_news_from_source(s) for s in FINANCE_NEWS_SOURCES]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_news = []
    source_stats = {}

    for s, r in zip(FINANCE_NEWS_SOURCES, results):
        name = s["name"]
        if isinstance(r, list):
            all_news.extend(r)
            source_stats[name] = len(r)
        else:
            source_stats[name] = 0

    # 按时间排序
    all_news.sort(key=lambda x: x.get("publish_time", ""), reverse=True)

    return all_news[:20], source_stats


# ========== 路由处理 ==========
async def handle_news(request, db: DB):
    """新闻列表 API"""
    # 解析查询参数
    query_params = request.query
    page = int(query_params.get("page", [1])[0]) if query_params.get("page") else 1
    page_size = (
        int(query_params.get("page_size", [10])[0])
        if query_params.get("page_size")
        else 10
    )

    # 限制范围
    page = max(1, page)
    page_size = max(5, min(50, page_size))

    try:
        # 抓取新新闻
        new_news, source_stats = await fetch_new_news()

        # 插入数据库
        new_hashes, inserted = await db.insert_news(new_news)

        # 获取分页数据
        total = await db.count()
        offset = (page - 1) * page_size
        all_news = await db.get_news(limit=page_size, offset=offset)

        return Response.json(
            {
                "success": True,
                "data": all_news,
                "total": total,
                "page": page,
                "page_size": page_size,
                "new_hashes": new_hashes,
                "new_count": inserted,
                "source_stats": source_stats,
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    except Exception as e:
        return Response.json(
            {"success": False, "message": f"获取新闻失败：{str(e)}", "data": []},
            status=500,
        )


async def handle_export_json(request, db: DB):
    """导出 JSON"""
    query_params = request.query
    start_date = query_params.get("start_date", [None])[0]
    end_date = query_params.get("end_date", [None])[0]

    news = await db.get_all_for_export(start_date, end_date)
    data = json.dumps(news, ensure_ascii=False, indent=2)

    fn = f"news_{start_date or 'all'}_{end_date or 'all'}.json"

    return Response(
        data,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={fn}"},
    )


async def handle_export_html(request, db: DB):
    """导出 HTML"""
    query_params = request.query
    start_date = query_params.get("start_date", [None])[0]
    end_date = query_params.get("end_date", [None])[0]

    news = await db.get_all_for_export(start_date, end_date)
    date_range = f"{start_date or '最早'} ~ {end_date or '最新'}"

    rows = ""
    for n in news:
        color = SOURCE_COLORS.get(n["source"], "#3498db")
        rows += f"""<tr>
<td style="padding:8px;border:1px solid #ddd;">{n["title"]}</td>
<td style="padding:8px;border:1px solid #ddd;"><span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;">{n["source"]}</span></td>
<td style="padding:8px;border:1px solid #ddd;">{n["publish_time"]}</td>
<td style="padding:8px;border:1px solid #ddd;">{n["intro"][:80]}</td>
</tr>"""

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>财经新闻导出</title>
<style>body{{font-family:sans-serif;margin:20px;background:#f5f5f5;}}table{{border-collapse:collapse;background:#fff;width:100%;}}th{{background:#2c3e50;color:#fff;padding:10px;text-align:left;}}tr:nth-child(even){{background:#f9f9f9;}}</style></head>
<body><h2>财经新闻导出 - {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</h2>
<p>时间范围：{date_range} | 共 {len(news)} 条新闻</p>
<table><tr><th>标题</th><th>来源</th><th>时间</th><th>摘要</th></tr>{rows}</table></body></html>"""

    fn = f"news_{start_date or 'all'}_{end_date or 'all'}.html"

    return Response(
        html,
        media_type="text/html",
        headers={"Content-Disposition": f"attachment; filename={fn}"},
    )


async def handle_export_check(request, db: DB):
    """验证导出接口"""
    query_params = request.query
    start_date = query_params.get("start_date", [None])[0]
    end_date = query_params.get("end_date", [None])[0]

    news = await db.get_all_for_export(start_date, end_date)

    return Response.json(
        {
            "success": True,
            "count": len(news),
            "date_range": f"{start_date or '最早'} ~ {end_date or '最新'}",
        }
    )


async def handle_export_dates(request, db: DB):
    """获取可用日期"""
    dates = await db.get_dates()

    return Response.json(
        {
            "success": True,
            "dates": dates,
            "min_date": dates[-1] if dates else None,
            "max_date": dates[0] if dates else None,
        }
    )


async def handle_health(request, db: DB):
    """健康检查"""
    total = await db.count()

    return Response.json(
        {
            "status": "healthy",
            "service": "财经新闻展示系统 (Cloudflare Workers)",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.8.0-cf",
            "news_in_db": total,
            "source_colors": SOURCE_COLORS,
        }
    )


async def handle_reset(request, db: DB):
    """重置新闻"""
    await db.reset()
    for k in source_last_ts:
        source_last_ts[k] = 0

    return Response.json({"success": True, "message": "已重置"})


# ========== 主入口 ==========
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        # 获取 D1 数据库绑定
        d1 = self.env.get("DB")

        if not d1:
            return Response.json({"error": "D1 database not bound"}, status=500)

        db = DB(d1)

        # 解析路径
        path = urlparse(request.url).path
        query = urlparse(request.url).query

        # 静态文件服务
        if path == "/" or path == "/index.html":
            return Response.redirect("/static/index.html")

        if path.startswith("/static/"):
            # 简化：直接返回静态文件（实际需要 Workers Sites 或 R2）
            filename = path.replace("/static/", "")
            return Response.redirect(f"/static/{filename}")

        # API 路由
        if path == "/api/news":
            return await handle_news(request, db)

        if path == "/api/export/json":
            return await handle_export_json(request, db)

        if path == "/api/export/html":
            return await handle_export_html(request, db)

        if path == "/api/export/check":
            return await handle_export_check(request, db)

        if path == "/api/export/dates":
            return await handle_export_dates(request, db)

        if path == "/api/health":
            return await handle_health(request, db)

        if path == "/api/news/reset" and request.method == "POST":
            return await handle_reset(request, db)

        # 默认返回
        return Response.redirect("/static/index.html")
