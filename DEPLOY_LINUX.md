# PioneerNews Linux 部署指南

## 一键部署

### 快速部署

```bash
# 1. 上传项目到服务器
scp -r PioneerNews/ user@server:/path/to/deploy

# 2. SSH 登录服务器
ssh user@server

# 3. 进入部署目录
cd /path/to/deploy/PioneerNews

# 4. 运行一键部署脚本
bash deploy.sh --auto

# 5. 查看部署状态
systemctl status pioneernews
```

### 交互式部署

```bash
bash deploy.sh
```

脚本会提示你输入端口号等信息。

### 指定端口

```bash
bash deploy.sh --port 8080
```

## 手动部署

如果不使用一键部署脚本，可以按以下步骤手动部署：

### 1. 安装系统依赖

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv curl wget build-essential libssl-dev libffi-dev
```

**CentOS/RHEL 7:**
```bash
sudo yum install -y python3 python3-pip curl wget gcc gcc-c++ make openssl-devel libffi-devel
```

**CentOS/RHEL 8+:**
```bash
sudo dnf install -y python3 python3-pip curl wget gcc gcc-c++ make openssl-devel libffi-devel
```

**Alpine:**
```bash
sudo apk add --no-cache python3 py3-pip curl wget build-base openssl-dev libffi-dev
```

### 2. 创建虚拟环境

```bash
cd /path/to/PioneerNews
python3 -m venv venv
source venv/bin/activate
```

### 3. 安装 Python 依赖

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. 启动服务

```bash
# 测试运行
python main.py

# 或使用环境变量指定端口
PORT=8080 python main.py
```

### 5. 配置 systemd 服务

创建服务文件 `/etc/systemd/system/pioneernews.service`:

```ini
[Unit]
Description=PioneerNews 财经新闻实时播报系统
After=network.target
Wants=network.target

[Service]
Type=simple
User=your_username
Group=your_group
WorkingDirectory=/path/to/PioneerNews
Environment="PATH=/path/to/PioneerNews/venv/bin"
ExecStart=/path/to/PioneerNews/venv/bin/python /path/to/PioneerNews/main.py
Environment="PORT=10842"
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=pioneernews

# 安全加固
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/path/to/PioneerNews

[Install]
WantedBy=multi-user.target
```

> **注意**: 需要将 `your_username`、`your_group` 和 `/path/to/PioneerNews` 替换为实际值。

启用并启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable pioneernews
sudo systemctl start pioneernews
sudo systemctl status pioneernews
```

### 6. 配置防火墙

**firewalld (CentOS/RHEL):**
```bash
sudo firewall-cmd --permanent --add-port=10842/tcp
sudo firewall-cmd --reload
```

**ufw (Ubuntu/Debian):**
```bash
sudo ufw allow 10842/tcp
```

**iptables:**
```bash
sudo iptables -A INPUT -p tcp --dport 10842 -j ACCEPT
sudo service iptables save
```

## 常用管理命令

### 服务管理

```bash
# 查看状态
systemctl status pioneernews

# 启动服务
sudo systemctl start pioneernews

# 停止服务
sudo systemctl stop pioneernews

# 重启服务
sudo systemctl restart pioneernews

# 禁用开机自启
sudo systemctl disable pioneernews

# 启用开机自启
sudo systemctl enable pioneernews
```

### 日志查看

```bash
# 查看实时日志
sudo journalctl -u pioneernews -f

# 查看最近 100 行日志
sudo journalctl -u pioneernews -n 100

# 查看今天的日志
sudo journalctl -u pioneernews --since today

# 查看错误日志
sudo journalctl -u pioneernews -p err
```

### 健康检查

```bash
# 检查服务是否正常运行
curl http://localhost:10842/api/health

# 检查端口是否监听
ss -tuln | grep 10842
# 或
netstat -tuln | grep 10842
```

### 数据管理

```bash
# 重置新闻数据
curl -X POST http://localhost:10842/api/news/reset

# 导出 JSON 格式
curl http://localhost:10842/api/export/json > news.json

# 导出 HTML 格式
curl http://localhost:10842/api/export/html > news.html

# 查看数据库大小
ls -lh news.db
```

## 更新部署

### 方法一：拉取最新代码后重启

```bash
cd /path/to/PioneerNews
git pull
sudo systemctl restart pioneernews
```

### 方法二：手动更新

```bash
cd /path/to/PioneerNews
# 上传更新的文件
source venv/bin/activate
pip install -r requirements.txt  # 如果依赖有变化
sudo systemctl restart pioneernews
```

## 卸载

### 使用脚本卸载

```bash
bash deploy.sh --uninstall
```

### 手动卸载

```bash
# 停止并删除服务
sudo systemctl stop pioneernews
sudo systemctl disable pioneernews
sudo rm /etc/systemd/system/pioneernews.service
sudo systemctl daemon-reload

# 删除部署目录（可选）
rm -rf /path/to/PioneerNews
```

## 故障排查

### 服务启动失败

```bash
# 查看详细错误
sudo journalctl -u pioneernews -n 50 --no-pager

# 检查端口是否被占用
ss -tuln | grep 10842

# 检查 Python 环境
/path/to/PioneerNews/venv/bin/python --version
```

### 无法访问服务

```bash
# 检查服务状态
systemctl status pioneernews

# 检查防火墙规则
sudo firewall-cmd --list-all  # firewalld
sudo ufw status              # ufw

# 检查本地是否能访问
curl http://localhost:10842/api/health

# 检查云服务器安全组规则（如阿里云、腾讯云等）
```

### 内存不足

```bash
# 查看内存使用
free -h

# 查看服务内存
systemctl status pioneernews

# 如果内存不足 512MB，建议关闭其他占用内存的程序
```

### 数据库损坏

```bash
# 备份当前数据库
cp news.db news.db.backup

# 重置数据库
curl -X POST http://localhost:10842/api/news/reset
sudo systemctl restart pioneernews
```

## 安全建议

1. **使用普通用户运行服务**，不要使用 root
2. **配置防火墙**，只开放必要的端口
3. **定期更新系统**：`sudo apt-get update && sudo apt-get upgrade`
4. **备份数据库**：定期复制 `news.db` 到其他位置
5. **使用 Nginx 反向代理**（可选）：
   ```nginx
   server {
       listen 80;
       server_name your-domain.com;
       
       location / {
           proxy_pass http://127.0.0.1:10842;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```
6. **配置 HTTPS**（推荐）：使用 Let's Encrypt 免费证书
   ```bash
   sudo apt-get install certbot python3-certbot-nginx
   sudo certbot --nginx -d your-domain.com
   ```

## 性能优化

### 调整端口

编辑 `deploy.sh` 或 systemd 服务文件中的 `PORT` 环境变量。

### 限制数据库大小

在 `main.py` 中修改 `MAX_DB_SIZE_MB` 参数（默认 500MB）。

### 监控资源使用

```bash
# 查看 CPU 使用率
top -p $(pgrep -f pioneernews)

# 查看内存使用
ps aux | grep pioneernews

# 查看磁盘使用
du -sh /path/to/PioneerNews
```

---

**如有问题，请查看日志**: `sudo journalctl -u pioneernews -f`
