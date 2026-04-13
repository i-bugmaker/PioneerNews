import os
import time
import tracemalloc
from datetime import datetime

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

tracemalloc.start()

app = FastAPI(title="财经新闻实时展示", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ========== 每个源的最后一条时间戳记录 ==========
# 用于增量拉取：只请求比 last_timestamp 更新的数据
source_last_ts: dict[str, int] = {
    "新浪财经": 0,
    "财联社": 0,
    "同花顺": 0,
    "东方财富": 0,
}


# ========== 新闻源配置 ==========
FINANCE_NEWS_SOURCES = [
    {
        "name": "新浪财经",
        "url": "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=15",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn/",
            "Accept": "application/json"
        }
    },
    {
        "name": "财联社",
        "url": "https://www.cls.cn/nodeapi/updateTelegraphList?rn=20&last_time=",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.cls.cn/",
            "Accept": "application/json"
        }
    },
    {
        "name": "同花顺",
        "url": "https://news.10jqka.com.cn/tapp/news/push/stock",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "http://news.10jqka.com.cn/",
            "Accept": "application/json"
        },
        "params": {"page": 1, "tag": "", "type": "all"}
    },
    {
        "name": "东方财富",
        "url": "https://np-listapi.eastmoney.com/comm/web/getFastNewsList",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://kuaixun.eastmoney.com/",
            "Accept": "application/json"
        },
        "params": {
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "sortEnd": "",
            "pageSize": 20,
        }
    }
]


# ========== 新闻抓取逻辑 ==========
async def fetch_news_from_source(source: dict) -> list:
    """从单个信息源异步获取新闻，只返回比 last_ts 更新的新闻"""
    news_list = []
    source_name = source["name"]
    last_ts = source_last_ts.get(source_name, 0)

    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            kwargs = {"url": source["url"], "headers": source["headers"]}
            if "params" in source:
                kwargs["params"] = dict(source["params"])
            # 防缓存
            if "params" in kwargs:
                kwargs["params"]["req_trace"] = str(int(time.time() * 1000))

            response = await client.get(**kwargs)
            if response.status_code != 200:
                return news_list

            data = response.json()

            if source_name == "新浪财经":
                articles = data.get("result", {}).get("data", [])
                for article in articles:
                    ctime = article.get("ctime", "")
                    ts = int(ctime) if ctime and str(ctime).isdigit() else 0
                    if ts <= last_ts:
                        continue
                    try:
                        publish_time = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    news_list.append({
                        "title": (article.get("title") or "无标题").strip(),
                        "url": article.get("url", "#"),
                        "source": source_name,
                        "publish_time": publish_time,
                        "intro": (article.get("intro", "") or "")[:150]
                    })

            elif source_name == "财联社":
                roll_data = data.get("data", {}).get("roll_data", [])
                for article in roll_data:
                    ctime = article.get("ctime", "")
                    ts = int(ctime) if ctime and str(ctime).isdigit() else 0
                    if ts <= last_ts:
                        continue
                    try:
                        publish_time = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    title = (article.get("title") or article.get("brief", "") or "无标题").strip()[:50]
                    news_list.append({
                        "title": title or "无标题",
                        "url": f"https://www.cls.cn/detail/{article.get('id', '')}" if article.get("id") else (article.get("shareurl", "#")),
                        "source": source_name,
                        "publish_time": publish_time,
                        "intro": (article.get("brief", "") or article.get("content", "") or "")[:150]
                    })

            elif source_name == "同花顺":
                articles = data.get("data", {}).get("list", [])
                for article in articles:
                    ctime = article.get("ctime", "")
                    ts = int(ctime) if ctime and str(ctime).isdigit() else 0
                    if ts <= last_ts:
                        continue
                    try:
                        publish_time = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    news_list.append({
                        "title": (article.get("title") or "无标题").strip(),
                        "url": article.get("shareUrl", article.get("url", "#")),
                        "source": source_name,
                        "publish_time": publish_time,
                        "intro": (article.get("digest", "") or article.get("short", "") or "")[:150]
                    })

            elif source_name == "东方财富":
                news_data = data.get("data", {}).get("fastNewsList", [])
                for article in news_data:
                    show_time = article.get("showTime", "")
                    try:
                        dt = datetime.strptime(show_time[:19], "%Y-%m-%d %H:%M:%S")
                        ts = int(dt.timestamp())
                    except:
                        ts = 0
                    if ts <= last_ts:
                        continue
                    try:
                        publish_time = show_time[:19] if show_time else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    code = article.get("code", "")
                    news_list.append({
                        "title": (article.get("title") or "无标题").strip(),
                        "url": f"https://finance.eastmoney.com/a/{code}.html" if code else "#",
                        "source": source_name,
                        "publish_time": publish_time,
                        "intro": (article.get("summary", "") or "")[:150]
                    })

    except Exception as e:
        print(f"获取{source_name}失败：{str(e)}")

    # 更新该源的最后时间戳
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


async def fetch_all_news() -> tuple:
    """并发获取所有信息源的最新新闻（只拉比上次更新的）"""
    import asyncio

    tasks = [fetch_news_from_source(source) for source in FINANCE_NEWS_SOURCES]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_news = []
    source_stats = {}
    for source, result in zip(FINANCE_NEWS_SOURCES, results):
        name = source["name"]
        if isinstance(result, list):
            all_news.extend(result)
            source_stats[name] = len(result)
        else:
            source_stats[name] = 0

    # 按时间倒序
    all_news.sort(key=lambda x: x.get("publish_time", ""), reverse=True)
    return all_news[:30], source_stats


# ========== 路由定义 ==========

@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/api/news")
async def get_news_api():
    try:
        new_news, source_stats = await fetch_all_news()
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": new_news,
                "total": len(new_news),
                "source_stats": source_stats,
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"获取新闻失败：{str(e)}", "data": []}
        )


@app.get("/api/health")
async def health_check():
    current, peak = tracemalloc.get_traced_memory()
    return {
        "status": "healthy",
        "service": "财经新闻展示系统",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.4.0",
        "memory_kb": round(current / 1024, 2),
        "source_timestamps": source_last_ts
    }


@app.post("/api/news/reset")
async def reset_news():
    """重置所有源时间戳，下次全量重新拉取"""
    for key in source_last_ts:
        source_last_ts[key] = 0
    return {"success": True, "message": "已重置，下次将全量拉取"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False, workers=1, log_level="info")
