import os
import io
import re
import time
import json
import asyncio
import sqlite3
import tracemalloc
from datetime import datetime

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

tracemalloc.start()

app = FastAPI(title="财经新闻实时展示", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news.db")
MAX_DB_SIZE_MB = 500  # 数据库最大 500MB


# ========== SQLite ==========
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT,
            source TEXT NOT NULL,
            publish_time TEXT,
            intro TEXT,
            title_hash TEXT UNIQUE,
            created_at TEXT
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_time ON news(publish_time DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_created ON news(created_at ASC)')
    conn.commit()
    return conn


def db_insert_news(news_list):
    if not news_list:
        return [], 0
    conn = get_db()
    c = conn.cursor()
    new_hashes = []
    inserted = 0
    for n in news_list:
        title_hash = f"{n['title'][:30]}|{n['source']}"
        try:
            c.execute('''
                INSERT OR IGNORE INTO news (title, url, source, publish_time, intro, title_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (n['title'], n['url'], n['source'], n['publish_time'], n['intro'],
                  title_hash, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            if c.rowcount > 0:
                new_hashes.append(title_hash)
                inserted += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    return new_hashes, inserted


def db_get_news(limit=10, offset=0):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT title, url, source, publish_time, intro FROM news ORDER BY publish_time DESC LIMIT ? OFFSET ?', (limit, offset))
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


def db_count():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM news')
    count = c.fetchone()[0]
    conn.close()
    return count


def db_get_all_for_export(start_date=None, end_date=None):
    conn = get_db()
    c = conn.cursor()
    query = 'SELECT title, url, source, publish_time, intro FROM news WHERE 1=1'
    params = []
    if start_date:
        query += ' AND publish_time >= ?'
        params.append(start_date)
    if end_date:
        query += ' AND publish_time <= ?'
        params.append(end_date + ' 23:59:59')
    query += ' ORDER BY publish_time DESC'
    c.execute(query, params)
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


def db_cleanup_if_needed():
    """检查数据库文件大小，超过 MAX_DB_SIZE_MB 时删除最旧的数据"""
    if not os.path.exists(DB_PATH):
        return
    size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    if size_mb < MAX_DB_SIZE_MB:
        return
    conn = get_db()
    c = conn.cursor()
    # 删除最旧的 20% 数据
    c.execute('SELECT COUNT(*) FROM news')
    total = c.fetchone()[0]
    to_delete = int(total * 0.2)
    if to_delete > 0:
        c.execute('SELECT id FROM news ORDER BY created_at ASC LIMIT ?', (to_delete,))
        ids = [row[0] for row in c.fetchall()]
        c.execute('DELETE FROM news WHERE id IN ({})'.format(','.join('?' * len(ids))), ids)
        conn.commit()
        print(f"数据库清理: 删除 {len(ids)} 条最旧数据")
    conn.close()
    # VACUUM 回收空间
    conn = sqlite3.connect(DB_PATH)
    conn.execute('VACUUM')
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
        "headers": {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/", "Accept": "application/json"}
    },
    {
        "name": "财联社",
        "url": "https://www.cls.cn/nodeapi/updateTelegraphList?rn=20&last_time=",
        "headers": {"User-Agent": "Mozilla/5.0", "Referer": "https://www.cls.cn/", "Accept": "application/json"}
    },
    {
        "name": "同花顺",
        "url": "https://news.10jqka.com.cn/tapp/news/push/stock",
        "headers": {"User-Agent": "Mozilla/5.0", "Referer": "http://news.10jqka.com.cn/", "Accept": "application/json"},
        "params": {"page": 1, "tag": "", "type": "all"}
    },
    {
        "name": "东方财富",
        "url": "https://np-listapi.eastmoney.com/comm/web/getFastNewsList",
        "headers": {"User-Agent": "Mozilla/5.0", "Referer": "https://kuaixun.eastmoney.com/", "Accept": "application/json"},
        "params": {"client": "web", "biz": "web_724", "fastColumn": "102", "sortEnd": "", "pageSize": 20}
    },
    {
        "name": "GDELT",
        "url": "https://api.gdeltproject.org/api/v2/doc/doc",
        "headers": {"User-Agent": "Mozilla/5.0"},
        "params": {"query": "finance economy stock market", "mode": "artlist", "format": "json", "maxrecords": 100}
    },
    {
        "name": "雅虎财经",
        "url": "https://query1.finance.yahoo.com/v1/finance/search",
        "headers": {"User-Agent": "Mozilla/5.0"},
        "params": {"q": "finance", "quotesCount": 10, "newsCount": 20}
    },
    {
        "name": "Google News",
        "url": "https://news.google.com/rss?topic=b&hl=en-US&gl=US&ceid=US:en",
        "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
    },
]


# ========== 抓取 ==========
async def fetch_news_from_source(source: dict) -> list:
    news_list = []
    source_name = source["name"]
    last_ts = source_last_ts.get(source_name, 0)
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            kwargs = {"url": source["url"], "headers": source["headers"]}
            method = source.get("method", "GET")
            if "params" in source:
                params_dict = dict(source["params"])
                if method == "GET":
                    kwargs["params"] = params_dict
                    kwargs["params"]["req_trace"] = str(int(time.time() * 1000))
                else:
                    kwargs["data"] = params_dict
            
            # 判断 GET 还是 POST
            if method == "POST":
                response = await client.post(**kwargs)
            else:
                response = await client.get(**kwargs)
            
            if response.status_code != 200:
                return news_list
            
            # Google News 返回的是 RSS XML，需要特殊处理
            if source_name == "Google News":
                soup = BeautifulSoup(response.text, 'xml')
                items = soup.find_all('item')
                for item in items:
                    title_tag = item.find('title')
                    source_tag = item.find('source')
                    pub_date_tag = item.find('pubDate')
                    link_tag = item.find('link')
                    desc_tag = item.find('description')
                    
                    # 标题格式: "标题 - 来源"，需要分割
                    full_title = title_tag.text if title_tag else ""
                    parts = full_title.rsplit(' - ', 1)
                    if len(parts) == 2:
                        clean_title, source_from_title = parts
                    else:
                        clean_title = full_title
                        source_from_title = ""
                    
                    # 获取来源
                    source_from_tag = source_tag.text if source_tag else source_from_title
                    
                    # 解析日期 (RFC 822 格式: "Tue, 14 Apr 2026 01:15:00 GMT")
                    pub_date = pub_date_tag.text if pub_date_tag else ""
                    try:
                        dt = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %Z")
                        pt = dt.strftime("%Y-%m-%d %H:%M:%S")
                        ts = int(dt.timestamp())
                    except:
                        pt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        ts = 0
                    
                    if ts <= last_ts: continue
                    
                    # 获取链接
                    link = link_tag.text if link_tag else "#"
                    
                    # 获取描述（HTML 格式，提取纯文本作为简介）
                    desc_html = desc_tag.text if desc_tag else ""
                    intro = ""
                    if desc_html:
                        # 用 BeautifulSoup 提取纯文本
                        desc_soup = BeautifulSoup(desc_html, 'html.parser')
                        # 获取第一个链接的文本（新闻标题）
                        first_link = desc_soup.find('a')
                        if first_link and first_link.parent.name == 'li':
                            intro = first_link.parent.get_text(strip=True)[:150]
                        else:
                            intro = desc_soup.get_text(strip=True)[:150]
                    
                    news_list.append({
                        "title": clean_title.strip() or "无标题",
                        "url": link,
                        "source": source_name,
                        "publish_time": pt,
                        "intro": f"[{source_from_tag}] {intro}" if source_from_tag else intro
                    })
                # Google News 处理完毕，跳过后续 JSON 解析
            else:
                # 其他源用 JSON 解析
                data = response.json()

            if source_name == "新浪财经":
                for a in data.get("result", {}).get("data", []):
                    ctime = a.get("ctime", "")
                    ts = int(ctime) if ctime and str(ctime).isdigit() else 0
                    if ts <= last_ts: continue
                    pt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    news_list.append({"title": (a.get("title") or "无标题").strip(), "url": a.get("url", "#"), "source": source_name, "publish_time": pt, "intro": (a.get("intro","") or "")[:150]})

            elif source_name == "财联社":
                for a in data.get("data", {}).get("roll_data", []):
                    ctime = a.get("ctime", "")
                    ts = int(ctime) if ctime and str(ctime).isdigit() else 0
                    if ts <= last_ts: continue
                    pt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    title = (a.get("title") or a.get("brief","") or "无标题").strip()[:50]
                    news_list.append({"title": title or "无标题", "url": f"https://www.cls.cn/detail/{a.get('id','')}" if a.get("id") else (a.get("shareurl","#")), "source": source_name, "publish_time": pt, "intro": (a.get("brief","") or a.get("content","") or "")[:150]})

            elif source_name == "同花顺":
                for a in data.get("data", {}).get("list", []):
                    ctime = a.get("ctime", "")
                    ts = int(ctime) if ctime and str(ctime).isdigit() else 0
                    if ts <= last_ts: continue
                    pt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    # 转换分享链接为静态链接
                    share_url = a.get("shareUrl", "")
                    url = "#"
                    if share_url and "/share/" in share_url:
                        m = re.search(r'/share/(\d+)/?', share_url)
                        if m:
                            aid = m.group(1)
                            date_str = datetime.fromtimestamp(ts).strftime("%Y%m%d") if ts else "unknown"
                            url = f"https://news.10jqka.com.cn/{date_str}/c{aid}.shtml"
                        else:
                            url = share_url
                    elif share_url:
                        url = share_url
                    news_list.append({"title": (a.get("title") or "无标题").strip(), "url": url, "source": source_name, "publish_time": pt, "intro": (a.get("digest","") or a.get("short","") or "")[:150]})

            elif source_name == "东方财富":
                for a in data.get("data", {}).get("fastNewsList", []):
                    st = a.get("showTime", "")
                    try:
                        dt = datetime.strptime(st[:19], "%Y-%m-%d %H:%M:%S")
                        ts = int(dt.timestamp())
                    except:
                        ts = 0
                    if ts <= last_ts: continue
                    pt = st[:19] if st else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    code = a.get("code", "")
                    news_list.append({"title": (a.get("title") or "无标题").strip(), "url": f"https://finance.eastmoney.com/a/{code}.html" if code else "#", "source": source_name, "publish_time": pt, "intro": (a.get("summary","") or "")[:150]})

            elif source_name == "GDELT":
                # GDELT 国际新闻
                items = data.get("articles", [])
                for a in items:
                    st = a.get("seendate", "")
                    ts = 0
                    try:
                        # GDELT 时间格式 "20250413T143000Z"
                        dt = datetime.strptime(st[:15], "%Y%m%dT%H%M%S")
                        ts = int(dt.timestamp())
                    except:
                        pass
                    if ts <= last_ts: continue
                    pt = f"{st[:4]}-{st[4:6]}-{st[6:8]} {st[9:11]}:{st[11:13]}:{st[13:15]}" if len(st) >= 15 else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    title = (a.get("title", "") or "无标题").strip()
                    news_list.append({"title": title, "url": a.get("url", "#"), "source": source_name, "publish_time": pt, "intro": (a.get("sourceurl", "") or a.get("domain", "") or "")[:150]})

            elif source_name == "雅虎财经":
                # 雅虎财经新闻
                items = data.get("news", [])
                for a in items:
                    pub = a.get("publisher", "")
                    # 雅虎用 providerPublishTime（Unix 时间戳）
                    pub_time = a.get("providerPublishTime", 0)
                    ts = 0
                    try:
                        if pub_time and isinstance(pub_time, (int, float)):
                            ts = int(pub_time)
                    except:
                        pass
                    if ts <= last_ts: continue
                    pt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    title = (a.get("title", "") or "无标题").strip()
                    # Yahoo 新闻链接
                    link = a.get("link", "") or a.get("url", "#")
                    news_list.append({"title": title, "url": link, "source": source_name, "publish_time": pt, "intro": f"[{pub}]" if pub else ""})
    except Exception as e:
        print(f"获取{source_name}失败：{str(e)}")

    if news_list:
        timestamps = []
        for n in news_list:
            try:
                dt = datetime.strptime(n["publish_time"], "%Y-%m-%d %H:%M:%S")
                timestamps.append(int(dt.timestamp()))
            except: pass
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
    all_news.sort(key=lambda x: x.get("publish_time", ""), reverse=True)
    return all_news[:20], source_stats


# ========== 路由 ==========
@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse("static/favicon.png", media_type="image/png")


@app.get("/api/news")
async def get_news_api(page: int = Query(1, ge=1), page_size: int = Query(10, ge=5, le=50)):
    try:
        new_news, source_stats = await fetch_new_news()
        new_hashes, inserted = db_insert_news(new_news)

        total = db_count()
        offset = (page - 1) * page_size
        all_news = db_get_news(limit=page_size, offset=offset)

        return JSONResponse(status_code=200, content={
            "success": True, "data": all_news, "total": total,
            "page": page, "page_size": page_size,
            "new_hashes": new_hashes, "new_count": inserted,
            "source_stats": source_stats,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"获取新闻失败：{str(e)}", "data": []})


@app.get("/api/export/json")
async def export_json(start_date: str = Query(None), end_date: str = Query(None)):
    news = db_get_all_for_export(start_date, end_date)
    data = json.dumps(news, ensure_ascii=False, indent=2)
    fn = f"news_{start_date or 'all'}_{end_date or 'all'}.json"
    return StreamingResponse(io.BytesIO(data.encode("utf-8")), media_type="application/json",
                            headers={"Content-Disposition": f"attachment; filename={fn}"})


@app.get("/api/export/check")
async def export_check(start_date: str = Query(None), end_date: str = Query(None)):
    """验证接口：只返回新闻数量和基本信息，不返回完整数据"""
    news = db_get_all_for_export(start_date, end_date)
    return {"success": True, "count": len(news),
            "date_range": f"{start_date or '最早'} ~ {end_date or '最新'}"}


@app.get("/api/export/dates")
async def export_dates():
    """返回数据库中存在新闻的所有日期（用于禁用日期选择器）"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT DISTINCT substr(publish_time, 1, 10) as d FROM news ORDER BY d DESC')
    dates = [row[0] for row in c.fetchall()]
    conn.close()
    return {"success": True, "dates": dates, "min_date": dates[-1] if dates else None, "max_date": dates[0] if dates else None}


@app.get("/api/export/html")
async def export_html(start_date: str = Query(None), end_date: str = Query(None)):
    news = db_get_all_for_export(start_date, end_date)
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
    return StreamingResponse(io.BytesIO(html.encode("utf-8")), media_type="text/html",
                            headers={"Content-Disposition": f"attachment; filename={fn}"})


@app.get("/api/health")
async def health_check():
    current, _ = tracemalloc.get_traced_memory()
    db_size_mb = round(os.path.getsize(DB_PATH) / (1024*1024), 2) if os.path.exists(DB_PATH) else 0
    return {"status": "healthy", "service": "财经新闻展示系统", "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.8.0", "memory_kb": round(current/1024, 2), "news_in_db": db_count(),
            "db_size_mb": db_size_mb, "source_colors": SOURCE_COLORS}


@app.post("/api/news/reset")
async def reset_news():
    conn = get_db(); conn.execute('DELETE FROM news'); conn.commit(); conn.close()
    for k in source_last_ts: source_last_ts[k] = 0
    return {"success": True, "message": "已重置"}


if __name__ == "__main__":
    # 启动时检查并清理数据库
    db_cleanup_if_needed()

    port = int(os.environ.get("PORT", 10842))
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False, workers=1, log_level="info")
