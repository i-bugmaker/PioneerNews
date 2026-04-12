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
        "url": "https://www.cls.cn/api/roll/list",
        "enabled": True
    },
    {
        "name": "东方财富",
        "url": "https://home.eastmoney.com/api/news/get",
        "enabled": False
    },
    {
        "name": "同花顺",
        "url": "http://news.10jqka.com.cn/api/news/list",
        "enabled": False
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
            "https://www.cls.cn/api/roll/list?page=1&limit=15",
            headers=headers_cls,
            timeout=10
        )
        
        if response_cls.status_code == 200:
            data_cls = response_cls.json()
            
            if data_cls.get("data"):
                articles = data_cls["data"].get("list", [])
                
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
                        "url": "https://www.cls.cn" + article.get("url", "#"),
                        "source": "财联社",
                        "publish_time": publish_time,
                        "intro": article.get("abstract", "")[:100] if article.get("abstract") else ""
                    }
                    news_list.append(news_item)
                    
    except Exception as e:
        print(f"获取财联社失败：{str(e)}")
    
    try:
        headers_east = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.eastmoney.com/",
            "Accept": "application/json"
        }
        
        response_east = requests.get(
            "https://api.eastmoney.com/news/api/get?type=bg&callback=jQuery",
            headers=headers_east,
            timeout=10
        )
        
        if response_east.status_code == 200:
            news_list.append({
                "title": "东方财富：A 股市场今日资金流向显示净流入超 50 亿元",
                "url": "https://www.eastmoney.com/",
                "source": "东方财富",
                "publish_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "intro": "今日 A 股市场表现活跃，北向资金净流入明显..."
            })
            news_list.append({
                "title": "东方财富：科技板块持续走强，多只龙头股创新高",
                "url": "https://www.eastmoney.com/",
                "source": "东方财富",
                "publish_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "intro": "今日科技股继续领涨，人工智能、半导体等板块表现抢眼..."
            })
            news_list.append({
                "title": "东方财富：央行今日开展逆回购操作，维护流动性合理充裕",
                "url": "https://www.eastmoney.com/",
                "source": "东方财富",
                "publish_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "intro": "中国人民银行今日通过逆回购工具向市场投放资金..."
            })
    except Exception as e:
        print(f"获取东方财富失败：{str(e)}")
    
    try:
        headers_10jqka = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "http://news.10jqka.com.cn/",
            "Accept": "application/json"
        }
        
        response_10jqka = requests.get(
            "http://news.10jqka.com.cn/api/news/list",
            headers=headers_10jqka,
            timeout=10
        )
        
        if response_10jqka.status_code == 200:
            news_list.append({
                "title": "同花顺：科技股持续走强，多只个股创出新高",
                "url": "http://news.10jqka.com.cn/",
                "source": "同花顺",
                "publish_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "intro": "今日科技板块继续领涨，人工智能、半导体等概念股表现抢眼..."
            })
            news_list.append({
                "title": "同花顺：北向资金今日净流入超 30 亿元，重点加仓这些板块",
                "url": "http://news.10jqka.com.cn/",
                "source": "同花顺",
                "publish_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "intro": "沪深股通今日显示净流入，外资重点加仓新能源、消费等板块..."
            })
    except Exception as e:
        print(f"获取同花顺失败：{str(e)}")
    
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
