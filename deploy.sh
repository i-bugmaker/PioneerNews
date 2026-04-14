#!/bin/bash
###############################################################################
# PioneerNews 一键部署脚本 (Linux)
# 功能：自动检测环境、安装依赖、配置服务、启动应用
# 用法：bash deploy.sh [OPTIONS]
#
# 选项：
#   --port <PORT>       设置端口号 (默认: 10842)
#   --auto              全自动模式，无需交互
#   --uninstall         卸载服务
#   --help              显示帮助信息
#
# 示例：
#   bash deploy.sh                  # 交互式部署
#   bash deploy.sh --auto           # 全自动部署
#   bash deploy.sh --port 8080      # 指定端口部署
#   bash deploy.sh --uninstall      # 卸载服务
###############################################################################

set -e  # 遇到错误立即退出

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 默认配置
DEFAULT_PORT=10842
PORT=$DEFAULT_PORT
AUTO_MODE=false
UNINSTALL=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="pioneernews"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
DEPLOY_DIR="$SCRIPT_DIR"
PYTHON_BIN=""
VENV_DIR="$DEPLOY_DIR/venv"

###############################################################################
# 工具函数
###############################################################################

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

log_success() {
    echo -e "${CYAN}[OK]${NC} $1"
}

# 检查是否为 root
check_root() {
    if [[ $EUID -eq 0 ]]; then
        log_warn "不建议使用 root 用户运行，建议使用普通用户部署"
        sleep 2
    fi
}

# 确认操作
confirm() {
    if [ "$AUTO_MODE" = true ]; then
        return 0
    fi
    read -p "$1 [y/N]: " -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]]
}

# 检查命令是否存在
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# 检测包管理器
detect_package_manager() {
    if command_exists apt-get; then
        echo "apt-get"
    elif command_exists yum; then
        echo "yum"
    elif command_exists dnf; then
        echo "dnf"
    elif command_exists apk; then
        echo "apk"
    elif command_exists pacman; then
        echo "pacman"
    else
        echo ""
    fi
}

# 检测系统发行版
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$NAME
        OS_VERSION=$VERSION_ID
    elif [ -f /etc/redhat-release ]; then
        OS=$(cat /etc/redhat-release)
    else
        OS=$(uname -s)
    fi
    echo "$OS"
}

###############################################################################
# 环境检测与准备
###############################################################################

check_system_requirements() {
    log_step "检测系统环境..."
    
    OS=$(detect_os)
    log_info "操作系统: $OS"
    
    # 检查内存 (至少 512MB)
    TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    TOTAL_MEM_MB=$((TOTAL_MEM_KB / 1024))
    log_info "可用内存: ${TOTAL_MEM_MB}MB"
    
    if [ "$TOTAL_MEM_MB" -lt 256 ]; then
        log_warn "内存不足 256MB，可能影响服务运行"
        if [ "$AUTO_MODE" = false ]; then
            confirm "是否继续？" || exit 1
        fi
    fi
    
    # 检查磁盘空间 (至少 500MB)
    AVAIL_DISK_KB=$(df -k "$DEPLOY_DIR" | tail -1 | awk '{print $4}')
    AVAIL_DISK_MB=$((AVAIL_DISK_KB / 1024))
    log_info "可用磁盘: ${AVAIL_DISK_MB}MB"
    
    if [ "$AVAIL_DISK_MB" -lt 500 ]; then
        log_error "磁盘空间不足 500MB"
        exit 1
    fi
    
    # 检测包管理器
    PKG_MANAGER=$(detect_package_manager)
    if [ -n "$PKG_MANAGER" ]; then
        log_info "包管理器: $PKG_MANAGER"
    else
        log_warn "未检测到常见包管理器，可能需要手动安装依赖"
    fi
}

check_python() {
    log_step "检查 Python 环境..."
    
    # 优先使用 Python 3.11, 3.10, 3.9, 3.8
    PYTHON_VERSIONS=("python3.11" "python3.10" "python3.9" "python3.8" "python3")
    
    for py in "${PYTHON_VERSIONS[@]}"; do
        if command_exists "$py"; then
            PYTHON_BIN="$py"
            break
        fi
    done
    
    if [ -z "$PYTHON_BIN" ]; then
        log_error "未找到 Python 3.8+，正在安装..."
        install_python
    else
        PYTHON_VERSION=$($PYTHON_BIN --version 2>&1 | awk '{print $2}')
        log_info "找到 Python: $PYTHON_VERSION"
        
        # 检查版本是否 >= 3.8
        PY_MAJOR=$($PYTHON_BIN -c "import sys; print(sys.version_info.major)")
        PY_MINOR=$($PYTHON_BIN -c "import sys; print(sys.version_info.minor)")
        
        if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 8 ]); then
            log_error "Python 版本过低 (需要 3.8+)，当前: $PYTHON_VERSION"
            exit 1
        fi
    fi
}

install_python() {
    local pkg_mgr=$1
    
    case "$pkg_mgr" in
        apt-get)
            apt-get update && apt-get install -y python3 python3-pip python3-venv
            ;;
        yum)
            yum install -y python3 python3-pip
            ;;
        dnf)
            dnf install -y python3 python3-pip
            ;;
        apk)
            apk add --no-cache python3 py3-pip
            ;;
        pacman)
            pacman -Sy --noconfirm python python-pip
            ;;
        *)
            log_error "无法自动安装 Python，请手动安装 Python 3.8+"
            exit 1
            ;;
    esac
    
    PYTHON_BIN="python3"
    log_success "Python 安装完成"
}

install_system_dependencies() {
    log_step "安装系统依赖..."
    
    local pkg_mgr=$(detect_package_manager)
    
    if [ -z "$pkg_mgr" ]; then
        log_warn "跳过系统依赖安装（未检测到包管理器）"
        return
    fi
    
    case "$pkg_mgr" in
        apt-get)
            apt-get update
            apt-get install -y curl wget build-essential libssl-dev libffi-dev
            ;;
        yum)
            yum install -y curl wget gcc gcc-c++ make openssl-devel libffi-devel
            ;;
        dnf)
            dnf install -y curl wget gcc gcc-c++ make openssl-devel libffi-devel
            ;;
        apk)
            apk add --no-cache curl wget build-base openssl-dev libffi-dev
            ;;
        pacman)
            pacman -Sy --noconfirm curl wget base-devel openssl libffi
            ;;
    esac
    
    log_success "系统依赖安装完成"
}

###############################################################################
# 应用部署
###############################################################################

setup_venv() {
    log_step "配置 Python 虚拟环境..."
    
    # 如果虚拟环境已存在，先检查是否可用
    if [ -d "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/python" ]; then
        log_info "检测到现有虚拟环境"
        if [ "$AUTO_MODE" = false ]; then
            if confirm "是否重新创建虚拟环境？"; then
                rm -rf "$VENV_DIR"
            else
                log_info "使用现有虚拟环境"
                return
            fi
        fi
    fi
    
    log_info "创建虚拟环境..."
    $PYTHON_BIN -m venv "$VENV_DIR"
    
    if [ $? -ne 0 ]; then
        log_error "创建虚拟环境失败"
        log_info "尝试安装 python3-venv..."
        local pkg_mgr=$(detect_package_manager)
        if [ -n "$pkg_mgr" ]; then
            case "$pkg_mgr" in
                apt-get) apt-get install -y python3-venv ;;
                yum) yum install -y python3-venv ;;
                dnf) dnf install -y python3-venv ;;
            esac
            $PYTHON_BIN -m venv "$VENV_DIR"
        else
            exit 1
        fi
    fi
    
    log_success "虚拟环境创建完成"
}

install_dependencies() {
    log_step "安装 Python 依赖..."
    
    local venv_python="$VENV_DIR/bin/python"
    local venv_pip="$VENV_DIR/bin/pip"
    
    # 升级 pip
    log_info "升级 pip..."
    $venv_python -m pip install --upgrade pip -q
    
    # 检查 requirements.txt
    if [ ! -f "$DEPLOY_DIR/requirements.txt" ]; then
        log_error "未找到 requirements.txt"
        exit 1
    fi
    
    # 安装依赖
    log_info "安装依赖包..."
    $venv_pip install -r "$DEPLOY_DIR/requirements.txt" -q --no-cache-dir
    
    if [ $? -ne 0 ]; then
        log_error "安装依赖失败"
        exit 1
    fi
    
    log_success "Python 依赖安装完成"
}

check_port_available() {
    local port=$1
    
    # 检查端口是否被占用
    if command_exists ss; then
        if ss -tuln | grep -q ":${port} "; then
            return 1
        fi
    elif command_exists netstat; then
        if netstat -tuln | grep -q ":${port} "; then
            return 1
        fi
    fi
    
    return 0
}

configure_port() {
    log_step "配置服务端口..."
    
    if [ "$AUTO_MODE" = false ]; then
        echo -e "${CYAN}请输入服务端口 [${DEFAULT_PORT}]:${NC}"
        read -p "> " input_port
        
        if [ -n "$input_port" ]; then
            if [[ "$input_port" =~ ^[0-9]+$ ]] && [ "$input_port" -ge 1 ] && [ "$input_port" -le 65535 ]; then
                PORT=$input_port
            else
                log_warn "无效的端口号，使用默认端口 ${DEFAULT_PORT}"
            fi
        fi
    fi
    
    # 检查端口可用性
    if ! check_port_available $PORT; then
        log_warn "端口 ${PORT} 已被占用"
        if [ "$AUTO_MODE" = false ]; then
            read -p "请输入其他端口: " -r NEW_PORT
            if [[ "$NEW_PORT" =~ ^[0-9]+$ ]] && [ "$NEW_PORT" -ge 1 ] && [ "$NEW_PORT" -le 65535 ]; then
                PORT=$NEW_PORT
            else
                log_error "无效的端口号"
                exit 1
            fi
        else
            # 自动模式：尝试使用默认端口 +1
            local try_port=$((PORT + 1))
            while ! check_port_available $try_port; do
                try_port=$((try_port + 1))
                if [ $try_port -gt 65535 ]; then
                    log_error "找不到可用端口"
                    exit 1
                fi
            done
            PORT=$try_port
        fi
    fi
    
    log_info "使用端口: ${PORT}"
}

###############################################################################
# systemd 服务配置
###############################################################################

detect_init_system() {
    if command_exists systemctl; then
        echo "systemd"
    elif [ -f /etc/init.d/functions ]; then
        echo "sysvinit"
    else
        echo "unknown"
    fi
}

get_current_user() {
    if [ -n "$SUDO_USER" ]; then
        echo "$SUDO_USER"
    else
        whoami
    fi
}

create_systemd_service() {
    log_step "配置 systemd 服务..."
    
    local current_user=$(get_current_user)
    local current_group=$(id -gn "$current_user" 2>/dev/null || echo "$current_user")
    local venv_python="$VENV_DIR/bin/python"
    local main_py="$DEPLOY_DIR/main.py"
    
    log_info "服务用户: $current_user"
    log_info "服务组: $current_group"
    
    # 创建服务文件
    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=PioneerNews 财经新闻实时播报系统
After=network.target
Wants=network.target

[Service]
Type=simple
User=${current_user}
Group=${current_group}
WorkingDirectory=${DEPLOY_DIR}
Environment="PATH=${VENV_DIR/bin}"
ExecStart=${venv_python} ${main_py}
Environment="PORT=${PORT}"
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

# 安全加固
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${DEPLOY_DIR}

[Install]
WantedBy=multi-user.target
EOF
    
    if [ $? -ne 0 ]; then
        log_error "创建服务文件失败，请检查权限"
        exit 1
    fi
    
    # 设置服务文件权限
    chmod 644 "$SERVICE_FILE"
    
    # 重新加载 systemd
    systemctl daemon-reload
    
    log_success "systemd 服务配置完成"
}

enable_and_start_service() {
    log_step "启动服务..."
    
    # 停止旧服务（如果正在运行）
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log_info "停止旧服务..."
        systemctl stop "$SERVICE_NAME"
    fi
    
    # 启用并启动服务
    systemctl enable "$SERVICE_NAME"
    systemctl start "$SERVICE_NAME"
    
    # 等待服务启动
    sleep 3
    
    # 检查服务状态
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log_success "服务启动成功"
    else
        log_error "服务启动失败"
        log_info "查看日志: journalctl -u $SERVICE_NAME -f"
        systemctl status "$SERVICE_NAME" --no-pager
        exit 1
    fi
}

###############################################################################
# 防火墙配置
###############################################################################

configure_firewall() {
    log_step "配置防火墙..."
    
    # 检测防火墙工具
    if command_exists firewall-cmd; then
        # firewalld (CentOS/RHEL)
        if firewall-cmd --state >/dev/null 2>&1; then
            log_info "配置 firewalld..."
            firewall-cmd --permanent --add-port=${PORT}/tcp >/dev/null 2>&1
            firewall-cmd --reload >/dev/null 2>&1
            log_success "firewalld 配置完成"
        fi
    elif command_exists ufw; then
        # ufw (Ubuntu/Debian)
        if ufw status >/dev/null 2>&1; then
            log_info "配置 ufw..."
            ufw allow ${PORT}/tcp >/dev/null 2>&1
            log_success "ufw 配置完成"
        fi
    elif command_exists iptables; then
        # iptables
        log_info "配置 iptables..."
        iptables -C INPUT -p tcp --dport ${PORT} -j ACCEPT >/dev/null 2>&1 || {
            iptables -A INPUT -p tcp --dport ${PORT} -j ACCEPT
            log_success "iptables 配置完成"
        }
    else
        log_warn "未检测到防火墙，请手动配置端口 ${PORT}"
    fi
}

###############################################################################
# 健康检查
###############################################################################

health_check() {
    log_step "执行健康检查..."
    
    local max_retries=5
    local retry_count=0
    
    while [ $retry_count -lt $max_retries ]; do
        local http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${PORT}/api/health" 2>/dev/null || echo "000")
        
        if [ "$http_code" = "200" ]; then
            local health_info=$(curl -s "http://localhost:${PORT}/api/health" 2>/dev/null)
            log_success "健康检查通过"
            log_info "服务信息: $health_info"
            return 0
        fi
        
        retry_count=$((retry_count + 1))
        log_warn "健康检查未通过，重试 ${retry_count}/${max_retries}..."
        sleep 2
    done
    
    log_error "健康检查失败，服务可能未正常启动"
    return 1
}

###############################################################################
# 卸载
###############################################################################

uninstall_service() {
    log_step "卸载服务..."
    
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        systemctl stop "$SERVICE_NAME"
        log_info "服务已停止"
    fi
    
    systemctl disable "$SERVICE_NAME" 2>/dev/null
    log_info "服务已禁用"
    
    if [ -f "$SERVICE_FILE" ]; then
        rm -f "$SERVICE_FILE"
        systemctl daemon-reload
        log_info "服务文件已删除"
    fi
    
    log_success "卸载完成"
    log_info "如需彻底删除，请手动删除部署目录: $DEPLOY_DIR"
}

###############################################################################
# 部署报告
###############################################################################

print_summary() {
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}        PioneerNews 部署完成！${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo -e "  访问地址: ${CYAN}http://<服务器IP>:${PORT}${NC}"
    echo -e "  服务状态: ${GREEN}运行中${NC}"
    echo -e "  服务名称: ${SERVICE_NAME}"
    echo -e "  部署目录: ${DEPLOY_DIR}"
    echo -e "  虚拟环境: ${VENV_DIR}"
    echo ""
    echo -e "${YELLOW}常用命令:${NC}"
    echo -e "  查看状态: ${CYAN}systemctl status ${SERVICE_NAME}${NC}"
    echo -e "  启动服务: ${CYAN}systemctl start ${SERVICE_NAME}${NC}"
    echo -e "  停止服务: ${CYAN}systemctl stop ${SERVICE_NAME}${NC}"
    echo -e "  重启服务: ${CYAN}systemctl restart ${SERVICE_NAME}${NC}"
    echo -e "  查看日志: ${CYAN}journalctl -u ${SERVICE_NAME} -f${NC}"
    echo -e "  查看端口: ${CYAN}ss -tuln | grep ${PORT}${NC}"
    echo ""
    echo -e "${YELLOW}健康检查:${NC}"
    echo -e "  ${CYAN}curl http://localhost:${PORT}/api/health${NC}"
    echo ""
    echo -e "${YELLOW}数据导出:${NC}"
    echo -e "  JSON: ${CYAN}http://<服务器IP>:${PORT}/api/export/json${NC}"
    echo -e "  HTML: ${CYAN}http://<服务器IP>:${PORT}/api/export/html${NC}"
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo ""
}

###############################################################################
# 参数解析
###############################################################################

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --port)
                PORT="$2"
                if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
                    log_error "无效的端口号: $PORT"
                    exit 1
                fi
                shift 2
                ;;
            --auto)
                AUTO_MODE=true
                shift
                ;;
            --uninstall)
                UNINSTALL=true
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "未知参数: $1"
                show_help
                exit 1
                ;;
        esac
    done
}

show_help() {
    echo "PioneerNews 一键部署脚本"
    echo ""
    echo "用法: bash deploy.sh [OPTIONS]"
    echo ""
    echo "选项:"
    echo "  --port <PORT>       设置端口号 (默认: 10842)"
    echo "  --auto              全自动模式，无需交互"
    echo "  --uninstall         卸载服务"
    echo "  --help              显示帮助信息"
    echo ""
    echo "示例:"
    echo "  bash deploy.sh                  # 交互式部署"
    echo "  bash deploy.sh --auto           # 全自动部署"
    echo "  bash deploy.sh --port 8080      # 指定端口部署"
    echo "  bash deploy.sh --uninstall      # 卸载服务"
}

###############################################################################
# 主流程
###############################################################################

main() {
    parse_args "$@"
    
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}   PioneerNews 一键部署脚本 v1.0${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    
    # 卸载模式
    if [ "$UNINSTALL" = true ]; then
        check_root
        uninstall_service
        exit 0
    fi
    
    # 部署模式
    check_root
    check_system_requirements
    check_python
    install_system_dependencies
    setup_venv
    install_dependencies
    configure_port
    
    # 确认部署
    if [ "$AUTO_MODE" = false ]; then
        echo ""
        echo -e "${YELLOW}配置汇总:${NC}"
        echo -e "  部署目录: ${DEPLOY_DIR}"
        echo -e "  端口: ${PORT}"
        echo -e "  Python: $($PYTHON_BIN --version 2>&1)"
        echo ""
        confirm "确认开始部署？" || exit 0
    fi
    
    # 配置服务
    local init_system=$(detect_init_system)
    
    if [ "$init_system" = "systemd" ]; then
        create_systemd_service
        enable_and_start_service
        configure_firewall
        health_check
    else
        log_warn "未检测到 systemd，无法自动配置服务"
        log_info "请手动启动服务: $VENV_DIR/bin/python $DEPLOY_DIR/main.py"
        log_info "端口: $PORT"
    fi
    
    # 打印部署报告
    print_summary
}

# 执行主流程
main "$@"
