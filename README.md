# 先锋新闻 · PioneerNews

从 13 个国内外财经信息源异步抓取新闻，存入 SQLite，前端 3 秒轮询加 DOM 差异渲染实现准实时更新。基于 FastAPI 构建，支持搜索、去重、数据导出等功能。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.8+-green.svg)
![FastAPI](https://img.shields.io/badge/fastapi-0.110.0-green.svg)
![Version](https://img.shields.io/badge/version-1.9.0-orange.svg)

## ✨ 功能特性

- 📰 **实时聚合** — 异步并发从 13 个信息源获取最新新闻
- 🔄 **3 秒轮询** — 无闪烁 DOM 差异渲染，新新闻自动插入顶部
- 💾 **持久化存储** — SQLite 本地数据库，重启不丢失数据
- 🔍 **全文搜索** — 支持标题、摘要、来源的模糊搜索
- 📄 **分页浏览** — 支持 5/10/20/30/50 条/页，无限滚动加载
- 📥 **数据导出** — 支持按日期段导出 JSON / HTML 格式
- 🧹 **自动清理** — 数据库超 500MB 自动清理最旧 20% 数据
- 🎨 **信息源着色** — 不同信息源使用不同主题色区分
- 🕐 **北京时间** — 头部实时显示北京时间（UTC+8）
- 📱 **响应式设计** — 支持 PC / 平板 / 手机端访问
- ⚡ **低内存** — 适配 512MB 小服务器运行
- 🤖 **智能去重** — SimHash 算法实现近义新闻识别与分组
- 🌙 **暗色模式** — 支持明暗主题切换
- 📊 **热度词云** — 基于新闻标题生成实时热词
- ❤️ **互动反馈** — 点击徽章发送表情反应，支持连击特效

## 📋 信息源

| 信息源 | 区域 | 抓取类型 | 状态 |
|--------|------|----------|------|
| 新浪财经 | 国内 | JSON API | ✅ |
| 财联社 | 国内 | JSON API | ✅ |
| 同花顺 | 国内 | JSON API | ✅ |
| 东方财富 | 国内 | JSON API | ✅ |
| 21经济网 | 国内 | JSON API | ✅ |
| 华尔街见闻 | 国内 | JSON API | ✅ |
| 雪球 | 国内 | HTML 抓取 | ✅ |
| 金十数据 | 国内 | JavaScript 变量 | ✅ |
| 格隆汇 | 国内 | HTML 抓取 | ✅ |
| 法布财经 | 国内 | HTML 抓取 | ✅ |
| 雅虎财经 | 国际 | RSS / XML | ✅ |
| GDELT | 国际 | JSON API | ✅ |
| Google News | 国际 | RSS / XML | ✅ |

## 🚀 快速开始

### 环境要求

- Python 3.8+

### Windows

```powershell
# 1. 创建虚拟环境
python -m venv venv
venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行（或双击 start.bat）
python main.py
```

### Linux / macOS

```bash
# 1. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行
python main.py
```

启动后访问：http://localhost:10842

## 📦 项目结构

```
PioneerNews/
├── main.py              # FastAPI 主程序（抓取 + API + SQLite）
├── requirements.txt     # Python 依赖
├── deploy.sh            # Linux 一键部署脚本
├── start.bat            # Windows 一键启动脚本
├── README.md            # 项目说明
├── .gitignore
└── static/
    ├── index.html       # 前端页面
    ├── style.css        # 样式文件
    ├── app.js           # 前端交互逻辑
    └── favicon.png      # 网站图标
```

运行时会在项目根目录自动生成 `news.db` 数据库文件（已加入 `.gitignore`）。

## 🔧 配置说明

### 端口

默认端口：`10842`，可通过环境变量 `PORT` 覆盖。

```bash
PORT=8080 python main.py
```

### 刷新间隔

前端轮询间隔，修改 `static/app.js`：

```javascript
const REFRESH_INTERVAL = 3000;  // 单位：毫秒
```

### 数据库清理阈值

修改 `main.py`：

```python
MAX_DB_SIZE_MB = 500  # 超过此大小自动清理最旧 20% 数据
```

## 🌐 Linux 部署

**一键部署：**

```bash
# 交互式部署
bash deploy.sh

# 全自动部署（无需交互）
bash deploy.sh --auto

# 指定端口
bash deploy.sh --port 8080

# 卸载
bash deploy.sh --uninstall
```

脚本会自动检测系统环境、安装依赖、配置 systemd/supervisor/sysvinit 服务，并处理防火墙和健康检查。

## 🐛 常见问题

**Q: 服务启动失败？**
检查端口是否被占用、Python 版本是否 ≥ 3.8、依赖是否安装完整。

**Q: 新闻无法加载？**
可能是网络问题、上游 API 变更或触发反爬机制。查看控制台日志获取详细错误。

**Q: 如何清空数据库？**
发送 `POST /api/news/reset`，或手动删除 `news.db` 后重启服务。

**Q: 数据库文件在哪？**
运行后会在项目根目录自动生成 `news.db`，已加入 `.gitignore` 不会提交到 Git。

## 🌐 API 接口

### 获取新闻列表

```
GET /api/news?page=1&page_size=10&source=新浪财经&search=关键词
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| page | int | 否 | 页码，默认 1 |
| page_size | int | 否 | 每页条数，默认 10 |
| source | string | 否 | 信息源过滤 |
| search | string | 否 | 搜索关键词 |

**响应示例：**
```json
{
  "success": true,
  "data": [
    {
      "title": "A股三大指数集体高开",
      "url": "https://finance.sina.com.cn/xxx",
      "source": "新浪财经",
      "publish_time": "2024-01-15 10:30:00",
      "publish_ts": 1705281000,
      "intro": "今日A股三大指数集体高开...",
      "dedup_group": 123,
      "dedup_count": 3
    }
  ],
  "total": 1250
}
```

### 搜索新闻

```
GET /api/search?query=关键词&page=1&page_size=10
```

### 轮询更新

```
GET /api/poll?since_ts=1705281000
```

### 导出数据

```
GET /api/export/json?start_date=2024-01-01&end_date=2024-01-31
GET /api/export/html?start_date=2024-01-01&end_date=2024-01-31
```

### 查看相似新闻

```
GET /api/dedup/group/{group_id}
```

### 获取热度词云

```
GET /api/trending
```

### 重置数据库

```
POST /api/news/reset
```

### 获取统计信息

```
GET /api/stats
```

## 📊 数据库结构

```sql
CREATE TABLE news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    url TEXT,
    source TEXT NOT NULL,
    publish_time TEXT,
    publish_ts INTEGER DEFAULT 0,
    intro TEXT,
    title_hash TEXT UNIQUE,
    created_at TEXT,
    title_full_hash TEXT,
    url_hash TEXT,
    simhash TEXT,
    dedup_group INTEGER DEFAULT 0
);
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键自增 |
| title | TEXT | 新闻标题 |
| url | TEXT | 原文链接 |
| source | TEXT | 信息源名称 |
| publish_time | TEXT | 发布时间字符串 |
| publish_ts | INTEGER | 发布时间戳 |
| intro | TEXT | 摘要内容 |
| title_hash | TEXT | 标题哈希（去重用） |
| created_at | TEXT | 入库时间 |
| title_full_hash | TEXT | 完整标题哈希 |
| url_hash | TEXT | URL 哈希 |
| simhash | TEXT | SimHash 值（近义去重） |
| dedup_group | INTEGER | 去重分组 ID |

## 🛠 技术栈

| 类别 | 技术 |
|------|------|
| 后端 | Python 3.8+, FastAPI, Uvicorn |
| HTTP | httpx（异步并发） |
| 解析 | BeautifulSoup4 + lxml（RSS/XML） |
| 存储 | SQLite3 |
| 前端 | 原生 HTML5 / CSS3 / JavaScript |
| 去重算法 | SimHash + Hamming Distance |
| 分词 | jieba（中文分词） |

## 📝 开发指南

### 添加新信息源

在 `main.py` 的 `FINANCE_NEWS_SOURCES` 列表中添加配置：

```python
{
    "name": "新信息源",
    "url": "https://api.example.com/news",
    "headers": {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://example.com/"
    },
    "params": {"page": 1, "limit": 20}  # 可选
}
```

然后在 `fetch_news_from_source()` 函数中添加对应的解析逻辑。

### 配置说明

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| PORT | 10842 | 服务端口 |
| TRACE_MALLOC | 空 | 启用内存追踪 |

### 运行测试

```bash
# 启动服务
python main.py

# 访问服务
http://localhost:10842

# 检查 API
curl http://localhost:10842/api/news
```

## 🐛 常见问题

**Q: 服务启动失败？**
检查端口是否被占用、Python 版本是否 ≥ 3.8、依赖是否安装完整。

**Q: 新闻无法加载？**
可能是网络问题、上游 API 变更或触发反爬机制。查看控制台日志获取详细错误。

**Q: 如何清空数据库？**
发送 `POST /api/news/reset`，或手动删除 `news.db` 后重启服务。

**Q: 数据库文件在哪？**
运行后会在项目根目录自动生成 `news.db`，已加入 `.gitignore` 不会提交到 Git。

**Q: 如何开启暗色模式？**
点击页面右上角的主题切换按钮，或系统自动跟随系统主题。

## 📄 许可证

MIT License

---

**Made with ❤️ using FastAPI**
