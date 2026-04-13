# QWEN.md - 财经新闻实时播报系统

## 项目概述

基于 **FastAPI** 的实时财经新闻展示系统。从 3 个财经信息源（新浪财经、财联社、同花顺）异步并发获取最新新闻，前端 5 秒轮询 + ETag 增量更新，实现准实时展示。

### 核心特性
- 📰 **实时获取** - 每次请求都真实调用 API，不阻塞
- 🔄 **5 秒轮询** - 前端 5 秒一次请求，配合 ETag 304 跳过无变化渲染
- 🆕 **新增标识** - 新新闻自动显示 `[新增]` 绿色标签
- 📱 **响应式设计** - 支持 PC 和移动端
- ⚡ **低内存** - 适配 512MB 服务器

### 技术栈
| 类别 | 技术 |
|------|------|
| 后端 | Python 3.8+, FastAPI 0.109.0, Uvicorn 0.27.0 |
| HTTP | httpx 0.27.0（异步并发） |
| 前端 | 原生 HTML5, CSS3, JavaScript |

---

## 项目结构

```
finance_news/
├── main.py              # FastAPI 主程序
├── requirements.txt     # Python 依赖 (fastapi, uvicorn, httpx)
├── README.md           # 项目说明
├── 部署指南.md          # WispByte 部署指南
├── QWEN.md             # 本文件
└── static/
    ├── index.html      # 前端页面
    ├── style.css       # 样式
    └── app.js          # 5秒轮询 + DOM增量更新
```

---

## 关键命令

```powershell
cd C:\Users\HuangMenghui\Desktop\finance_news
venv\Scripts\activate
python main.py
```

访问 http://localhost:8080

---

## API 接口

| 端点 | 说明 |
|------|------|
| `GET /` | 返回前端页面 |
| `GET /api/news?if_none_match={etag}` | 获取新闻列表（支持 304） |
| `GET /api/health` | 健康检查 |
| `GET /api/memory` | 内存状态 |
| `POST /api/cache/clear` | 清除缓存 |

### 新闻源（3 个已验证可用）

| 信息源 | 状态 |
|--------|------|
| 新浪财经 | ✅ 实时 |
| 财联社 | ✅ 实时 |
| 同花顺 | ✅ 实时 |

> 东方财富 API 已失效（返回 None），已从配置中移除。

### 实时性说明

- **无缓存阻塞**：每次 `/api/news` 请求都真实调用 3 个 API 源
- **ETag 优化**：数据未变化返回 304，前端跳过渲染
- **并发抓取**：3 个源同时请求，总耗时 ≈ 最慢那个（8s timeout）
- **前端轮询**：5 秒一次，页面不可见时暂停

---

## 优化历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0.0 | - | 初始版本，requests 串行请求 |
| v1.1.0 | - | httpx 异步、缓存、ETag、内存监控 |
| v1.2.0 | 2026-04-13 | 移除 WebSocket，改为 5 秒轮询；移除缓存阻塞 |
| v1.3.0 | 2026-04-13 | 新增 GDELT、雅虎财经两个国际新闻源；恢复东方财富源；金十数据因官方停用免费 API 已移除（共 6 个源） |
