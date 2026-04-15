# PioneerNews Cloudflare Workers 版本

财经新闻实时播报系统 - Cloudflare Workers + D1 版

## 功能特性

- 📰 实时新闻获取（7 个财经源）
- 🔄 自动增量刷新（3 秒）
- 💾 Cloudflare D1 SQLite 持久化
- 📄 分页浏览（5/10/20/30/50 条/页）
- 📥 数据导出（JSON/HTML）
- 🎨 信息源着色
- 🕐 北京时间实时时钟
- 📱 响应式设计

## 部署步骤

### 前置要求

1. **Node.js** + **npm**
2. **uv** (Python 包管理器)
3. **Cloudflare 账号**

### 1. 安装 pywrangler

```bash
npm install -g pywrangler
```

### 2. 登录 Cloudflare

```bash
pywrangler auth login
```

### 3. 创建 D1 数据库

```bash
pywrangler d1 create pioneer-news-db
```

### 4. 更新配置

编辑 `wrangler.toml`，将 `database_id` 替换为上一步创建的 ID：

```toml
[[d1_databases]]
binding = "DB"
database_name = "pioneer-news-db"
database_id = "替换为你的数据库ID"
```

### 5. 初始化数据库

```bash
pywrangler d1 execute pioneer-news-db --local --file=./schema.sql
```

### 6. 部署

```bash
pywrangler deploy
```

### 7. 访问

部署成功后，访问输出的 URL（如 `https://pioneer-news.your-account.workers.dev`）

## 本地开发

```bash
# 启动本地开发服务器
pywrangler dev

# 或指定端口
pywrangler dev --port 8787
```

## 项目结构

```
pioneer-news-cf/
├── wrangler.toml      # Cloudflare 配置
├── schema.sql          # D1 数据库 Schema
├── src/
│   └── entry.py       # Workers 入口 + 后端逻辑
├── static/
│   ├── index.html     # 前端页面
│   ├── style.css      # 样式
│   ├── app.js         # 前端逻辑
│   └── favicon.png    # 图标
└── README.md          # 本文件
```

## API 接口

| 接口 | 说明 |
|------|------|
| `GET /api/news?page=1&page_size=10` | 获取新闻列表 |
| `GET /api/export/json` | 导出 JSON |
| `GET /api/export/html` | 导出 HTML |
| `GET /api/export/check` | 验证导出数量 |
| `GET /api/export/dates` | 获取可用日期 |
| `GET /api/health` | 健康检查 |
| `POST /api/news/reset` | 重置数据 |

## 环境变量

无需额外环境变量，所有配置在 `wrangler.toml` 中完成。

## 注意事项

1. **D1 免费额度**: 每天 100,000 次读取，5,000 次写入
2. **Workers 免费额度**: 每天 100,000 次请求
3. **首次抓取**: 部署后首次访问会自动抓取新闻源

## 技术说明

- **Python 运行时**: Pyodide (WebAssembly)
- **数据库**: Cloudflare D1 (SQLite 兼容)
- **静态文件**: Workers 直接返回（需配置 Workers Sites 或 R2 存储）

---

**Made with ❤️ on Cloudflare Workers**
