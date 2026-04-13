# 财经新闻实时播报系统

一个基于 FastAPI 的实时财经新闻展示系统，支持多个财经信息源并发获取，SQLite 持久化存储，自动刷新，分页导出等功能。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.8+-green.svg)
![FastAPI](https://img.shields.io/badge/fastapi-0.109.0-green.svg)
![Version](https://img.shields.io/badge/version-1.6.0-orange.svg)

## ✨ 功能特性

- 📰 **实时新闻获取** - 异步并发从 4 个财经信息源获取最新新闻
- 🔄 **自动刷新** - 每 3 秒自动增量刷新，无闪烁 DOM 更新
- 💾 **SQLite 持久化** - 新闻存入本地数据库，刷新/重启服务不丢失
- 📄 **分页浏览** - 支持 5/10/20/30/50 条/页，可自由切换
- 📥 **数据导出** - 支持 JSON 和 HTML 格式，可按时间段筛选导出
- 🧹 **自动清理** - 数据库超 500MB 自动清理最旧数据
- 🫧 **冒泡动画** - 新新闻以冒泡弹出效果展示，旧新闻平滑挤压
- 🎨 **信息源着色** - 不同信息源使用官网主题色区分
- 🕐 **北京时间** - 头部实时显示北京时间（UTC+8）
- 📱 **响应式设计** - 支持 PC 和移动端访问

## 📋 信息源

| 信息源 | 状态 | 链接规则 |
|--------|------|----------|
| 新浪财经 | ✅ 实时获取 | API 直接返回原文链接 |
| 财联社 | ✅ 实时获取 | `https://www.cls.cn/detail/{id}` |
| 同花顺 | ✅ 实时获取 | `https://news.10jqka.com.cn/{date}/c{id}.shtml` |
| 东方财富 | ✅ 实时获取 | `https://finance.eastmoney.com/a/{code}.html` |

## 🚀 快速开始

### 环境要求

- Python 3.8+
- pip 包管理器

### 安装步骤

1. **克隆项目**
```bash
cd finance_news
```

2. **创建虚拟环境**
```bash
python -m venv venv
```

3. **激活虚拟环境**

Windows:
```bash
venv\Scripts\activate
```

macOS/Linux:
```bash
source venv/bin/activate
```

4. **安装依赖**
```bash
pip install -r requirements.txt
```

5. **运行服务**
```bash
python main.py
```

6. **访问系统**

打开浏览器访问：http://localhost:8080

## 📦 项目结构

```
finance_news/
├── main.py              # FastAPI 主程序（抓取 + API + SQLite）
├── requirements.txt     # Python 依赖
├── README.md           # 项目说明文档
├── 部署指南.md          # WispByte 部署指南
├── QWEN.md             # 项目上下文文档
├── news.db             # SQLite 数据库（自动生成，.gitignore）
└── static/
    ├── index.html      # 前端页面
    ├── style.css       # 样式文件
    ├── app.js          # JavaScript 交互逻辑
    └── favicon.svg     # 网站图标
```

## 🔧 配置说明

### 端口配置

默认端口：`8080`

修改端口（在 main.py 中）:
```python
port = int(os.environ.get("PORT", 8080))  # 默认 8080
```

### 刷新间隔

默认刷新间隔：`3 秒`

修改刷新间隔（在 static/app.js 中）:
```javascript
const REFRESH_INTERVAL = 3000;  // 单位：毫秒
```

### 数据库清理阈值

默认最大数据库大小：`500 MB`

修改阈值（在 main.py 中）:
```python
MAX_DB_SIZE_MB = 500  # 超过此大小自动清理最旧 20% 数据
```

## 🌐 部署

### WispByte 部署

详细部署步骤请查看：[部署指南.md](部署指南.md)

**快速部署步骤：**

1. 登录 https://wispbyte.com/
2. 创建 Free Plan Python 服务
3. 上传项目文件（不含 news.db 和 venv/）
4. 设置入口文件为 `main.py`
5. 启动服务

## 📡 API 接口

### 获取新闻列表（分页）

**请求：**
```
GET /api/news?page=1&page_size=10
```

**响应：**
```json
{
  "success": true,
  "data": [
    {
      "title": "新闻标题",
      "url": "https://...",
      "source": "新浪财经",
      "publish_time": "2026-04-13 15:30:00",
      "intro": "新闻摘要..."
    }
  ],
  "total": 150,
  "page": 1,
  "page_size": 10,
  "new_hashes": ["abc123|新浪财经", "def456|财联社"],
  "new_count": 2,
  "update_time": "2026-04-13 15:30:05"
}
```

### 导出数据

```
GET /api/export/json?start_date=2026-04-10&end_date=2026-04-13
GET /api/export/html?start_date=2026-04-10&end_date=2026-04-13
```

### 验证导出数量

```
GET /api/export/check?start_date=2026-04-10&end_date=2026-04-13
```

**响应：**
```json
{
  "success": true,
  "count": 85,
  "date_range": "2026-04-10 ~ 2026-04-13"
}
```

### 可用日期列表

```
GET /api/export/dates
```

**响应：**
```json
{
  "success": true,
  "dates": ["2026-04-13", "2026-04-12", "2026-04-11"],
  "min_date": "2026-04-11",
  "max_date": "2026-04-13"
}
```

### 健康检查

**请求：**
```
GET /api/health
```

**响应：**
```json
{
  "status": "healthy",
  "service": "财经新闻展示系统",
  "timestamp": "2026-04-13 15:30:05",
  "version": "1.6.0",
  "memory_kb": 6085.82,
  "news_in_db": 150,
  "db_size_mb": 0.04
}
```

## 🎨 信息源颜色

| 信息源 | 颜色 | 色值 |
|--------|------|------|
| 新浪财经 | 🔴 红 | `#E63B2E` |
| 财联社 | 🔴 红 | `#DC2626` |
| 同花顺 | 🟡 橙 | `#F59E0B` |
| 东方财富 | 🟠 橙 | `#FF6600` |

## 🐛 常见问题

### Q: 服务启动失败？

**A:** 检查以下几点：
- 端口是否被占用
- Python 版本是否 >= 3.8
- 依赖是否完整安装

### Q: 新闻无法加载？

**A:** 可能是：
- 网络连接问题
- API 接口变化
- 触发了反爬机制

### Q: 数据库文件在哪？

**A:** 运行服务后会在项目根目录自动生成 `news.db` 文件，已加入 `.gitignore` 不会被提交到 Git。

### Q: 如何清空数据库重新采集？

**A:** 发送 POST 请求：`POST /api/news/reset`，或手动删除 `news.db` 文件后重启服务。

## 📝 更新日志

### v1.6.0 (2026-04-13)

- ✅ SQLite 持久化存储，刷新/重启不丢失数据
- ✅ 数据库超限自动清理（500MB 限制）
- ✅ 分页功能：5/10/20/30/50 条/页
- ✅ 导出功能：JSON/HTML，支持时间段筛选
- ✅ 新新闻冒泡弹出动画 + 旧新闻挤压效果
- ✅ 信息源主题色区分
- ✅ 北京时间实时时钟
- ✅ 网站 favicon
- ✅ 3 秒增量无闪烁刷新
- ✅ 新增新闻绿色背景标记

### v1.0.0 (2024-04-12)

- ✅ 初始版本发布
- ✅ 支持多信息源新闻获取
- ✅ 60 秒自动刷新
- ✅ 响应式设计

## 📄 许可证

本项目采用 MIT 许可证。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

---

**Made with ❤️ using FastAPI**
