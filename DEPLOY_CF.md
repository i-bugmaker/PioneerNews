# Cloudflare Workers 部署指南

## 方式一：使用 Workers + D1（推荐）

### 步骤 1: 安装工具

```bash
# 安装 pywrangler (Cloudflare Python Workers CLI)
npm install -g pywrangler

# 安装 uv (Python 包管理器)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 步骤 2: 登录 Cloudflare

```bash
pywrangler auth login
```

### 步骤 3: 创建 D1 数据库

```bash
# 创建数据库
pywrangler d1 create pioneer-news-db

# 初始化表结构
pywrangler d1 execute pioneer-news-db --local --file=./schema.sql
```

### 步骤 4: 部署

```bash
pywrangler deploy
```

---

## 方式二：使用 Workers Sites（静态前端）

如果想把前端也托管在 Cloudflare，可以使用 Workers Sites：

### 1. 创建 `_headers` 文件

```
/*
  Content-Type: text/html
/static/*
  Content-Type: text/css
/static/app.js
  Content-Type: application/javascript
/static/favicon.png
  Content-Type: image/png
```

### 2. 更新 `wrangler.toml`

```toml
name = "pioneer-news"
main = "src/entry.py"
compatibility_date = "2026-04-15"
compatibility_flags = ["python_workers"]

[site]
bucket = "./static"

[[d1_databases]]
binding = "DB"
database_name = "pioneer-news-db"
database_id = "YOUR_DATABASE_ID"
```

### 3. 修改 `entry.py` 静态文件路由

```python
# 静态文件服务改为从 bucket 读取
if path.startswith("/static/"):
    return self.env.ASSETS.fetch(request)
```

---

## 常见问题

### Q: 如何查看日志？

```bash
pywrangler tail
```

### Q: 如何回滚版本？

```bash
pywrangler rollback
```

### Q: D1 免费额度够用吗？

| 操作 | 免费额度 |
|------|----------|
| 读取 | 100,000 次/天 |
| 写入 | 5,000 次/天 |
| 存储 | 5 MB |

对于个人新闻展示应用，足够了。

### Q: 可以绑定自定义域名吗？

可以，在 Cloudflare Dashboard 中添加自定义域名即可。

---

## 本地调试

```bash
# 启动本地开发
pywrangler dev

# 指定端口
pywrangler dev --port 8787

# 带热重载
pywrangler dev --live-reload
```

---

## 生产环境检查清单

- [ ] D1 数据库已创建并初始化
- [ ] wrangler.toml 中 database_id 已配置
- [ ] 已测试本地开发环境
- [ ] 已配置自定义域名（可选）
- [ ] 已设置 Cron Trigger 定时任务（可选）

---

**提示**: 首次部署后，访问网站会自动抓取新闻数据。建议先访问几次让数据充实起来。
