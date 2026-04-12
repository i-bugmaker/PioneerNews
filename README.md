# 财经新闻实时播报系统

一个基于 FastAPI 的实时财经新闻展示系统，支持多个财经信息源，自动刷新，新增新闻标识等功能。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.8+-green.svg)
![FastAPI](https://img.shields.io/badge/fastapi-0.109.0-green.svg)

## ✨ 功能特性

- 📰 **实时新闻获取** - 自动从多个财经信息源获取最新新闻
- 🔄 **自动刷新** - 每 60 秒自动刷新，保持信息最新
- 🆕 **新增标识** - 新新闻自动显示 `[新增]` 标签，绿色高亮
- 📱 **响应式设计** - 支持 PC 和移动端访问
- 🎨 **美观界面** - 现代化 UI 设计，渐变色彩
- ⚡ **快速加载** - 轻量级架构，秒级响应
- 🌐 **多信息源** - 支持新浪财经、财联社、东方财富、同花顺等

## 📋 信息源

| 信息源 | 状态 | 说明 |
|--------|------|------|
| 新浪财经 | ✅ 实时获取 | 通过 API 实时获取最新新闻 |
| 财联社 | ✅ 实时获取 | 通过 API 实时获取最新新闻 |
| 东方财富 | ⚠️ 示例数据 | 展示示例新闻，点击跳转官网 |
| 同花顺 | ⚠️ 示例数据 | 展示示例新闻，点击跳转官网 |

## 🚀 快速开始

### 环境要求

- Python 3.8+
- pip 包管理器

### 安装步骤

1. **克隆或下载项目**
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
├── main.py              # FastAPI 主程序
├── requirements.txt     # Python 依赖
├── README.md           # 项目说明文档
├── 部署指南.md          # WispByte 部署指南
└── static/
    ├── index.html      # 前端页面
    ├── style.css       # 样式文件
    └── app.js          # JavaScript 交互逻辑
```

## 🔧 配置说明

### 端口配置

默认端口：`8080`

修改端口（在 main.py 中）:
```python
port = int(os.environ.get("PORT", 8080))  # 默认 8080
```

### 刷新间隔

默认刷新间隔：`60 秒`

修改刷新间隔（在 static/app.js 中）:
```javascript
const REFRESH_INTERVAL = 60000;  // 单位：毫秒
```

### 新增新闻标识数量

默认标识前 3 条新新闻

修改标识数量（在 static/app.js 中）:
```javascript
const isNew = index < 3 && hasNewNews;  // 修改数字 3
```

## 🌐 部署

### WispByte 部署

详细部署步骤请查看：[部署指南.md](部署指南.md)

**快速部署步骤：**

1. 登录 https://wispbyte.com/
2. 创建 Free Plan Python 服务
3. 上传项目文件
4. 设置入口文件为 `main.py`
5. 启动服务

### 其他平台部署

本项目也可以部署到以下平台：

- **Heroku** - 创建 Procfile 文件
- **Railway** - 自动识别 Python 项目
- **Render** - 使用 render.yaml 配置
- **Vercel** - 需要适配 Serverless 函数

## 📡 API 接口

### 获取新闻列表

**请求：**
```
GET /api/news
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
      "publish_time": "2024-04-12 23:45:30",
      "intro": "新闻摘要..."
    }
  ],
  "total": 15,
  "update_time": "2024-04-12 23:46:00"
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
  "timestamp": "2024-04-12 23:46:00",
  "version": "1.0.0"
}
```

## 🎨 自定义

### 修改主题颜色

编辑 `static/style.css`:

```css
/* 主背景渐变 */
body {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

/* 头部背景渐变 */
header {
    background: linear-gradient(135deg, #2c3e50 0%, #3498db 100%);
}

/* 新增新闻背景色 */
.news-card.news-new {
    background: #e8f5e9;
    border-left-color: #4caf50;
}
```

### 修改网站标题

编辑 `static/index.html`:

```html
<h1>📈 财经新闻实时播报</h1>
<p class="subtitle">实时获取最新财经资讯</p>
```

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

### Q: 自动刷新不工作？

**A:** 检查：
- 浏览器标签页是否处于后台
- JavaScript 是否被禁用
- 控制台是否有错误信息

## 📝 更新日志

### v1.0.0 (2024-04-12)

- ✅ 初始版本发布
- ✅ 支持多信息源新闻获取
- ✅ 60 秒自动刷新
- ✅ 新增新闻标识功能
- ✅ 响应式设计
- ✅ WispByte 部署支持

## 📄 许可证

本项目采用 MIT 许可证。详见 [LICENSE](LICENSE) 文件。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📧 联系方式

如有问题或建议，请提交 Issue。

---

**Made with ❤️ using FastAPI**
