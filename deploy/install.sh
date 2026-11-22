#!/usr/bin/env bash
# Ubuntu 一键部署脚本（在 api-miao 项目根目录运行：bash deploy/install.sh）
# 会安装两个 systemd 服务：
#   apimiao.service           —— gunicorn Web（多 worker）
#   apimiao-scheduler.service —— Transmission 定时任务独立进程
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="${APIMIAO_USER:-www-data}"

echo "[APIMiao] 项目目录: $APP_DIR"

# 1. 系统依赖（Caddy 由 caddy-setup.sh 单独安装）
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip

# 2. Python 虚拟环境与依赖
if [ ! -d "$APP_DIR/.venv" ]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# 3. 配置文件：首次从模板生成 config.ini（含密钥，已 gitignore）
if [ ! -f "$APP_DIR/config.ini" ]; then
  cp "$APP_DIR/config.ini.example" "$APP_DIR/config.ini"
  echo "[APIMiao] 已生成 config.ini，请按需编辑（WebAuthn 域名、密钥等）。"
fi
sudo chown "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR/config.ini"

# 4. 权限：数据目录归服务用户（两个服务共用同一数据库）
mkdir -p "$APP_DIR/data"
sudo chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR/data"

# 5. systemd：Web 服务 + 调度器服务
for svc in apimiao apimiao-scheduler; do
  sed "s|__APP_DIR__|$APP_DIR|g; s|__SERVICE_USER__|$SERVICE_USER|g" \
    "$APP_DIR/deploy/$svc.service" | sudo tee "/etc/systemd/system/$svc.service" > /dev/null
done

sudo systemctl daemon-reload
sudo systemctl enable apimiao apimiao-scheduler
sudo systemctl restart apimiao-scheduler
sudo systemctl restart apimiao

echo "[APIMiao] 两个服务已启动："
echo "  sudo systemctl status apimiao"
echo "  sudo systemctl status apimiao-scheduler"
echo "[APIMiao] 下一步："
echo "  1) 编辑 $APP_DIR/config.ini：把 webauthn.rp_id / webauthn.origin 设为你的域名，"
echo "     security.secret_key 设为长随机串（openssl rand -hex 32），cookie_secure=true。"
echo "  2) 安装 Caddy 并自动申请证书：DOMAIN=your-domain.com EMAIL=you@example.com bash deploy/caddy-setup.sh"
echo "  3) 改完配置后重启：sudo systemctl restart apimiao apimiao-scheduler"
echo "  Caddy 会自动处理 HTTPS 证书申请与续期，无需 certbot。"
