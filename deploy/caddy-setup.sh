#!/usr/bin/env bash
# 安装 Caddy（官方 apt 源）并部署 APIMiao 反代配置。
# 用法：DOMAIN=your-domain.com EMAIL=you@example.com bash deploy/caddy-setup.sh
# EMAIL 用于 Let's Encrypt 证书账户注册与到期提醒；省略则不给邮箱（仍会自动签证书）。
# Caddy 会自动申请/续期 Let's Encrypt 证书，无需 certbot。
set -euo pipefail

DOMAIN="${DOMAIN:-your-domain.com}"
EMAIL="${EMAIL:-your-email@example.com}"
if [ "$DOMAIN" = "your-domain.com" ]; then
  echo "[APIMiao] 请通过环境变量指定域名：DOMAIN=example.com EMAIL=you@example.com bash deploy/caddy-setup.sh"
  exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[APIMiao] 安装 Caddy 官方 apt 源..."
sudo apt-get update -y
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list > /dev/null
sudo apt-get update -y
sudo apt-get install -y caddy

echo "[APIMiao] 生成 Caddyfile（域名: $DOMAIN，邮箱: $EMAIL）..."
sudo mkdir -p /var/log/caddy
sudo chown -R caddy:caddy /var/log/caddy
sed -e "s|your-domain.com|$DOMAIN|g" -e "s|your-email@example.com|$EMAIL|g" \
  "$APP_DIR/deploy/Caddyfile.example" | sudo tee /etc/caddy/Caddyfile > /dev/null

# 校验配置语法
sudo caddy validate --config /etc/caddy/Caddyfile

sudo systemctl enable caddy
sudo systemctl reload caddy || sudo systemctl restart caddy

echo "[APIMiao] Caddy 已配置完成（证书联系邮箱: $EMAIL）。证书会在首次访问 $DOMAIN 时自动申请。"
echo "[APIMiao] 查看状态: sudo systemctl status caddy ; 证书日志: journalctl -u caddy -f"
