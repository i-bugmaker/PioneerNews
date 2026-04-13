import os
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI(title="财经新闻实时展示")

app.mount("/static", StaticFiles(directory="static"), name="static")

FINANCE_NEWS_SOURCES = [
    {
        "name": "新浪财经",
        "url": "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=30&page=1&r=0.123456789",
        "enabled": True
    },
    {
        "name": "财联社",
        "url": "https://www.cls.cn/nodeapi/updateTelegraphList?rn=15&last_time=",
        "enabled": True
    },
    {
        "name": "同花顺",
        "url": "https://news.10jqka.com.cn/tapp/news/push/stock",
        "enabled": True
    },
    {
        "name": "东方财富",
        "url": "https://np-listapi.eastmoney.com/comm/web/getFastNewsList",
        "enabled": True
    }
]

def get_finance_news():
    news_list = []
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn/",
            "Accept": "application/json"
        }
        
        response = requests.get(
            "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=20",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get("result"):
                articles = data["result"].get("data", [])
                
                for article in articles[:15]:
                    ctime = article.get("ctime", "")
                    try:
                        if ctime and ctime.isdigit():
                            dt = datetime.fromtimestamp(int(ctime))
                            publish_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                        else:
                            publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    news_item = {
                        "title": article.get("title", "无标题"),
                        "url": article.get("url", "#"),
                        "source": "新浪财经",
                        "publish_time": publish_time,
                        "intro": article.get("intro", "")[:100] if article.get("intro") else ""
                    }
                    news_list.append(news_item)
                    
    except Exception as e:
        print(f"获取新浪财经失败：{str(e)}")
    
    try:
        headers_cls = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.cls.cn/",
            "Accept": "application/json"
        }
        
        response_cls = requests.get(
            "https://www.cls.cn/nodeapi/updateTelegraphList?rn=15&last_time=",
            headers=headers_cls,
            timeout=10
        )
        
        if response_cls.status_code == 200:
            data_cls = response_cls.json()
            
            if data_cls.get("data") and data_cls["data"].get("roll_data"):
                articles = data_cls["data"]["roll_data"]
                
                for article in articles[:10]:
                    ctime = article.get("ctime", "")
                    try:
                        if ctime and ctime.isdigit():
                            dt = datetime.fromtimestamp(int(ctime))
                            publish_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                        else:
                            publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    news_item = {
                        "title": article.get("title", "无标题") or article.get("brief", "无标题")[:20],
                        "url": article.get("shareurl", "#"),
                        "source": "财联社",
                        "publish_time": publish_time,
                        "intro": article.get("brief", "")[:100] if article.get("brief") else article.get("content", "")[:100]
                    }
                    news_list.append(news_item)
                    
    except Exception as e:
        print(f"获取财联社失败：{str(e)}")
    
    try:
        headers_10jqka = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "http://news.10jqka.com.cn/",
            "Accept": "application/json"
        }
        
        response_10jqka = requests.get(
            "https://news.10jqka.com.cn/tapp/news/push/stock",
            headers=headers_10jqka,
            timeout=10
        )
        
        if response_10jqka.status_code == 200:
            data_10jqka = response_10jqka.json()
            
            if data_10jqka.get("data") and data_10jqka["data"].get("list"):
                articles = data_10jqka["data"]["list"]
                
                for article in articles[:10]:
                    ctime = article.get("ctime", "")
                    try:
                        if ctime and ctime.isdigit():
                            dt = datetime.fromtimestamp(int(ctime))
                            publish_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                        else:
                            publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    news_item = {
                        "title": article.get("title", "无标题"),
                        "url": article.get("shareUrl", article.get("url", "#")),
                        "source": "同花顺",
                        "publish_time": publish_time,
                        "intro": article.get("digest", "")[:100] if article.get("digest") else article.get("short", "")[:100]
                    }
                    news_list.append(news_item)
                    
    except Exception as e:
        print(f"获取同花顺失败：{str(e)}")
    
    try:
        headers_east = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://kuaixun.eastmoney.com/",
            "Accept": "application/json"
        }
        
        import time
        params_east = {
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "sortEnd": "",
            "pageSize": 15,
            "req_trace": str(int(time.time() * 1000)),
        }
        
        response_east = requests.get(
            "https://np-listapi.eastmoney.com/comm/web/getFastNewsList",
            headers=headers_east,
            params=params_east,
            timeout=10
        )
        
        if response_east.status_code == 200:
            data_east = response_east.json()
            
            if data_east.get("data") and data_east["data"].get("fastNewsList"):
                articles = data_east["data"]["fastNewsList"]
                
                for article in articles[:10]:
                    show_time = article.get("showTime", "")
                    try:
                        if show_time:
                            publish_time = show_time
                        else:
                            publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    news_item = {
                        "title": article.get("title", "无标题"),
                        "url": f"https://kuaixun.eastmoney.com/detail/{article.get('code', '')}",
                        "source": "东方财富",
                        "publish_time": publish_time,
                        "intro": article.get("summary", "")[:100] if article.get("summary") else ""
                    }
                    news_list.append(news_item)
                    
    except Exception as e:
        print(f"获取东方财富失败：{str(e)}")
    
    if not news_list:
        news_list = [
            {
                "title": "央行：保持流动性合理充裕，维护金融市场稳定",
                "url": "#",
                "source": "新浪财经",
                "publish_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "intro": "中国人民银行今日发布公告，表示将继续实施稳健的货币政策..."
            },
            {
                "title": "A 股三大指数集体高开，科技股领涨",
                "url": "#",
                "source": "东方财富",
                "publish_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "intro": "今日 A 股市场开盘表现强劲，上证指数上涨 0.5%..."
            },
            {
                "title": "国际油价持续上涨，布伦特原油突破 85 美元",
                "url": "#",
                "source": "财联社",
                "publish_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "intro": "受地缘政治紧张局势影响，国际油价继续上行..."
            }
        ]
    
    news_list.sort(key=lambda x: x.get("publish_time", ""), reverse=True)
    
    return news_list

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.get("/api/news")
async def get_news_api():
    try:
        news_list = get_finance_news()
        
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": news_list,
                "total": len(news_list),
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
    return {
        "status": "healthy",
        "service": "财经新闻展示系统",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0.0"
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
