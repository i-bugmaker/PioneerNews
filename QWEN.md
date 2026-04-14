# QWEN.md - 财经新闻实时播报系统

## 项目概述

基于 **FastAPI** 的实时财经新闻展示系统。从 7 个财经信息源（新浪财经、财联社、同花顺、东方财富、GDELT、雅虎财经、Google News）异步并发获取最新新闻，前端 3 秒轮询 + DOM 差异渲染，实现准实时展示。

### 核心特性
- 📰 **实时获取** - 每次请求都真实调用 API，不阻塞
- 🔄 **3 秒轮询** - 前端 3 秒一次请求，配合 `new_hashes` 做差异渲染
- 🆕 **新增标识** - 新新闻自动显示 `[新增]` 绿色标签
- 📱 **响应式设计** - 支持 PC 和移动端
- ⚡ **低内存** - 适配 512MB 服务器
- 🗄️ **自动清理** - 启动时检查数据库大小，超过 500MB 自动清理最旧数据

### 技术栈
| 类别 | 技术 |
|------|------|
| 后端 | Python 3.8+, FastAPI 0.109.0, Uvicorn 0.27.0 |
| HTTP | httpx 0.27.0（异步并发） |
| 前端 | 原生 HTML5, CSS3, JavaScript |

---

## 项目结构

```
PioneerNews/
├── main.py              # FastAPI 主程序
├── requirements.txt     # Python 依赖 (fastapi, uvicorn, httpx)
├── README.md           # 项目说明
├── start.bat           # Windows 启动脚本
├── QWEN.md             # 本文件
└── static/
    ├── index.html      # 前端页面
    ├── style.css       # 样式
    └── app.js          # 3秒轮询 + DOM增量更新
```

---

## 关键命令

```powershell
cd C:\Users\HuangMenghui\Desktop\PioneerNews
venv\Scripts\activate
python main.py
```

访问 http://localhost:10842

---

## API 接口

| 端点 | 说明 |
|------|------|
| `GET /` | 返回前端页面 |
| `GET /api/news?page=1&page_size=10` | 获取新闻列表（支持分页） |
| `GET /api/health` | 健康检查 |
| `GET /api/export/json` | 导出 JSON 格式新闻 |
| `GET /api/export/html` | 导出 HTML 格式新闻 |
| `GET /api/export/check` | 验证导出（返回数量） |
| `GET /api/export/dates` | 返回有新闻的日期 |
| `POST /api/news/reset` | 重置新闻数据 |

### 新闻源（7 个）

| 信息源 | 状态 |
|--------|------|
| 新浪财经 | ✅ 实时 |
| 财联社 | ✅ 实时 |
| 同花顺 | ✅ 实时 |
| 东方财富 | ✅ 实时 |
| GDELT | ✅ 实时（国际） |
| 雅虎财经 | ✅ 实时（国际） |
| Google News | ✅ 实时（国际，RSS） |

### 实时性说明

- **无缓存阻塞**：每次 `/api/news` 请求都真实调用 7 个 API 源
- **增量更新**：通过 `new_hashes` 字段做 DOM 差异渲染，新新闻自动显示 `[新增]` 标签
- **并发抓取**：7 个源同时请求，总耗时 ≈ 最慢那个（8s timeout）
- **前端轮询**：3 秒一次，页面不可见时暂停
- **数据库清理**：启动时自动检查数据库大小，超过 500MB 时删除最旧 20% 数据

---

## 优化历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0.0 | - | 初始版本，requests 串行请求 |
| v1.1.0 | - | httpx 异步、缓存、ETag、内存监控 |
| v1.2.0 | 2026-04-13 | 移除 WebSocket，改为 5 秒轮询；移除缓存阻塞 |
| v1.3.0 | 2026-04-13 | 新增 GDELT、雅虎财经两个国际新闻源；恢复东方财富源；金十数据因官方停用免费 API 已移除（共 6 个源） |
| v1.4.0 | 2026-04-14 | 新增巨潮资讯源（沪深两市公告） |
| v1.5.0 | 2026-04-14 | 优化巨潮资讯时间戳解析 |
| v1.6.0 | 2026-04-14 | 修复巨潮资讯公告链接拼接 |
| v1.7.0 | 2026-04-14 | 新增 Google News 财经新闻源（RSS），共 8 个源；添加 BeautifulSoup4 依赖用于 XML 解析 |
| v1.8.0 | 2026-04-14 | 移除巨潮资讯源（公告时间非真实发布时间，storageTime 字段无数据），恢复 7 个源 |
