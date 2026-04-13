import os
import time
import tracemalloc
from datetime import datetime
from itertools import cycle

import httpx
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

# ========== 内存监控 ==========
tracemalloc.start()

# ========== 应用配置 ==========
app = FastAPI(title="财经新闻实时展示", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory="static"), name="static")

# 新闻源配置
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


# ========== 实时新闻管理器 ==========
class NewsManager:
    """模仿 AStock LiveMonitor 的去重和管理逻辑"""

    def __init__(self, max_news: int = 50):
        self.max_news = max_news
        self.news_list = []          # 已展示新闻列表
        self.seen_keys = set()       # 去重键集合

    def _make_key(self, title: str, source: str) -> str:
        """去重键：标题前30字 + | + 来源"""
        return f"{title[:30]}|{source}"

    def add_news(self, new_news: list) -> int:
        """添加新闻，返回新增数量"""
        new_count = 0
        for news in reversed(new_news):  # 倒序插入，最新的在前面
            title = news.get("title", "")
            source = news.get("source", "")
            key = self._make_key(title, source)

            if key not in self.seen_keys:
                self.seen_keys.add(key)
                self.news_list.insert(0, news)
                new_count += 1

        # 限制列表大小
        if len(self.news_list) > self.max_news:
            old_news = self.news_list[self.max_news:]
            self.news_list = self.news_list[:self.max_news]
            # 清理旧去重键（如果键只出现在旧新闻中）
            old_keys = set()
            for n in old_news:
                old_keys.add(self._make_key(n.get("title", ""), n.get("source", "")))
            current_keys = set()
            for n in self.news_list:
                current_keys.add(self._make_key(n.get("title", ""), n.get("source", "")))
            self.seen_keys = current_keys

        return new_count

    def get_news(self, limit: int = 30) -> list:
        return self.news_list[:limit]

    def clear(self):
        self.news_list.clear()
        self.seen_keys.clear()


news_manager = NewsManager(max_news=50)


# ========== 新闻抓取逻辑 ==========
async def fetch_news_from_source(source: dict) -> list:
    """从单个信息源异步获取新闻"""
    news_list = []
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            kwargs = {"url": source["url"], "headers": source["headers"]}
            if "params" in source:
                kwargs["params"] = source["params"]
            # 添加 req_trace 防缓存
            if "params" in kwargs:
                kwargs["params"]["req_trace"] = str(int(time.time() * 1000))

            response = await client.get(**kwargs)
            if response.status_code != 200:
                return news_list

            data = response.json()
            source_name = source["name"]

            if source_name == "新浪财经":
                articles = data.get("result", {}).get("data", [])
                for article in articles[:12]:
                    ctime = article.get("ctime", "")
                    try:
                        if ctime and str(ctime).isdigit():
                            dt = datetime.fromtimestamp(int(ctime))
                            publish_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                        else:
                            publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
                for article in roll_data[:12]:
                    ctime = article.get("ctime", "")
                    try:
                        if ctime and str(ctime).isdigit():
                            dt = datetime.fromtimestamp(int(ctime))
                            publish_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                        else:
                            publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
                for article in articles[:12]:
                    ctime = article.get("ctime", "")
                    try:
                        if ctime and str(ctime).isdigit():
                            dt = datetime.fromtimestamp(int(ctime))
                            publish_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                        else:
                            publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
                if not news_data:
                    return news_list

                for article in news_data[:12]:
                    show_time = article.get("showTime", "")
                    try:
                        if show_time:
                            publish_time = show_time[:19]
                        else:
                            publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
        print(f"获取{source['name']}失败：{str(e)}")

    return news_list


async def fetch_all_news() -> tuple:
    """并发获取所有信息源新闻，返回 (新闻列表, 各源统计)"""
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
    return all_news, source_stats


# ========== 路由定义 ==========

@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/api/news")
async def get_news_api():
    """获取新闻列表 - 每次请求都实时获取并去重"""
    try:
        # 实时获取
        news_list, source_stats = await fetch_all_news()

        # 去重并添加到管理器
        new_count = news_manager.add_news(news_list)

        # 返回去重后的新闻列表
        result_news = news_manager.get_news(limit=30)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": result_news,
                "total": len(result_news),
                "new_count": new_count,
                "source_stats": source_stats,
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": f"获取新闻失败：{str(e)}",
                "data": []
            }
        )


@app.get("/api/health")
async def health_check():
    """健康检查"""
    current, peak = tracemalloc.get_traced_memory()
    return {
        "status": "healthy",
        "service": "财经新闻展示系统",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.3.0",
        "memory_kb": round(current / 1024, 2),
        "news_in_memory": len(news_manager.news_list),
        "seen_keys": len(news_manager.seen_keys)
    }


@app.post("/api/news/clear")
async def clear_news():
    """清除新闻"""
    news_manager.clear()
    return {"success": True, "message": "新闻已清除"}


# ========== 启动入口 ==========

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False, workers=1, log_level="info")
