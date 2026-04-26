# 先锋新闻 · PioneerNews

一个基于 FastAPI 的实时财经新闻聚合展示系统。异步并发从 7 个国内外财经信息源获取最新新闻，SQLite 持久化存储，前端 3 秒轮询 + DOM 差异渲染，实现准实时展示。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.8+-green.svg)
![FastAPI](https://img.shields.io/badge/fastapi-0.109.0-green.svg)
![Version](https://img.shields.io/badge/version-1.8.0-orange.svg)

## ✨ 功能特性

- 📰 **实时聚合** — 异步并发从 7 个信息源获取最新新闻
- 🔄 **3 秒轮询** — 无闪烁 DOM 差异渲染，新新闻平滑插入
- 💾 **持久化存储** — SQLite 本地数据库，重启不丢失
- 📄 **分页浏览** — 支持 5/10/20/30/50 条/页
- 📥 **数据导出** — 支持按日期段导出 JSON / HTML
- 🧹 **自动清理** — 数据库超 500MB 自动清理最旧 20% 数据
- 🎨 **信息源着色** — 不同信息源使用不同主题色区分
- 🕐 **北京时间** — 头部实时显示北京时间（UTC+8）
- 📱 **响应式设计** — 支持 PC / 平板 / 手机端访问
- ⚡ **低内存** — 适配 512MB 小服务器运行

## 📋 信息源

| 信息源 | 区域 | 类型 |
|--------|------|------|
| 新浪财经 | 国内 | JSON API |
| 财联社 | 国内 | JSON API |
| 同花顺 | 国内 | JSON API |
| 东方财富 | 国内 | JSON API |
| 雅虎财经 | 国际 | JSON API |
| GDELT | 国际 | JSON API |
| Google News | 国际 | RSS / XML |

## 🚀 快速开始

### 环境要求

- Python 3.8+
- pip

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
├── start.bat            # Windows 启动脚本
├── DEPLOY_LINUX.md      # Linux 部署文档
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

详细部署步骤见 [DEPLOY_LINUX.md](DEPLOY_LINUX.md)。

**快速部署：**

```bash
# 一键部署（自动检测系统、安装依赖、配置 systemd）
bash deploy.sh --auto

# 指定端口
bash deploy.sh --port 8080

# 卸载
bash deploy.sh --uninstall
```

脚本支持 systemd / supervisor / sysvinit / nohup 四种运行方式，自动选择最合适的方案。

## 📡 API 接口

| 端点 | 说明 |
|------|------|
| `GET /` | 返回前端页面 |
| `GET /api/news?page=1&page_size=10` | 获取新闻列表（分页） |
| `GET /api/health` | 健康检查 |
| `GET /api/export/json` | 导出 JSON 格式新闻 |
| `GET /api/export/html` | 导出 HTML 格式新闻 |
| `GET /api/export/check` | 验证导出（仅返回数量） |
| `GET /api/export/dates` | 返回有新闻的日期列表 |
| `POST /api/news/reset` | 重置新闻数据 |

### 获取新闻示例

```json
{
  "success": true,
  "data": [
    {
      "title": "新闻标题",
      "url": "https://...",
      "source": "新浪财经",
      "publish_time": "2025-01-01 15:30:00",
      "intro": "新闻摘要..."
    }
  ],
  "total": 150,
  "page": 1,
  "page_size": 10,
  "new_hashes": ["abc...|新浪财经"],
  "new_count": 2,
  "update_time": "2025-01-01 15:30:05"
}
```

### 导出示例

```
GET /api/export/json?start_date=2025-01-01&end_date=2025-01-31
GET /api/export/html?start_date=2025-01-01&end_date=2025-01-31
```

### 健康检查示例

```json
{
  "status": "healthy",
  "service": "财经新闻展示系统",
  "version": "1.8.0",
  "memory_kb": 6085.82,
  "news_in_db": 150,
  "db_size_mb": 0.04
}
```

## 🎨 信息源主题色

| 信息源 | 色值 |
|--------|------|
| 新浪财经 | `#0891B2` |
| 财联社 | `#E11D48` |
| 同花顺 | `#F59E0B` |
| 东方财富 | `#FF6600` |
| GDELT | `#6366F1` |
| 雅虎财经 | `#00B4D8` |
| Google News | `#8B5CF6` |

## 🐛 常见问题

**Q: 服务启动失败？**
检查端口是否被占用、Python 版本是否 ≥ 3.8、依赖是否安装完整。

**Q: 新闻无法加载？**
可能是网络问题、上游 API 变更或触发反爬机制。查看控制台日志获取详细错误。

**Q: 如何清空数据库？**
发送 `POST /api/news/reset`，或手动删除 `news.db` 后重启服务。

**Q: 数据库文件在哪？**
运行后会在项目根目录自动生成 `news.db`，已加入 `.gitignore` 不会提交到 Git。

## 🛠 技术栈

| 类别 | 技术 |
|------|------|
| 后端 | Python 3.8+, FastAPI, Uvicorn |
| HTTP | httpx（异步并发） |
| 解析 | BeautifulSoup4 + lxml（RSS/XML） |
| 存储 | SQLite3 |
| 前端 | 原生 HTML5 / CSS3 / JavaScript |

## 📄 许可证

MIT License

---

**Made with ❤️ using FastAPI**
