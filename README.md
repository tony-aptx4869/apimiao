# API转转喵 (APIMiao)

一个自托管的 API 中转站平台。配置一次源 API 的调用方式，之后通过本平台统一调用即可——绕开频率限制、免费额度、IP 黑白名单等麻烦。

采用 MIT 开源协议。

## 快速开始

```bash
cd api-miao
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 main.py
```

首次运行会自动从 `config.ini.example` 生成 `config.ini`（含全部配置项，开箱即用）。需要改端口、域名、密钥时编辑 `config.ini` 即可，**无需设置任何环境变量**。

打开 <http://localhost:5099>。

首次运行会创建默认 ADMIN 账号 `miao@apimiao.local`（ID `0000005f`），**初始密码同时打印在终端和 `data/initial_password.txt`**。首次登录会强制修改密码，改完后请删除该文件。

如忘记密码，重置（会生成新随机密码并写入 `data/initial_password.txt`）：

```bash
python3 -c "from app.db import reset_admin_password; print(reset_admin_password())"
```

## 功能

- **双语界面**：右上角 Language 切换 English / 汉语，语言存 Cookie 不进 URL；日期时间按浏览器本地时区显示，数据库统一存 UTC。
- **登录**：邮箱+密码（可选 Google Authenticator 等 TOTP 2FA）；通行密钥（WebAuthn，兼容 Apple / Windows Hello / Google / YubiKey）。
- **2FA 恢复码**：启用 2FA 时生成 10 个一次性恢复码（哈希存储、仅显示一次），验证器丢失时可用恢复码登录，可在 Profile 查看剩余数量或重新生成。
- **权限**：ADMIN 可管理所有用户；USER 只能改自己的资料。
- **API Keys**：UUID 格式，创建后中央弹窗完整显示一次（背景调暗、手动关闭），之后仅显示头尾各 4 位。
- **Control Panel**：Overview（近 1h/24h/7d/30d 用量）、Logs（30 天统计、每日折线图、Top 9 Endpoints、过滤与调用历史）。日志区分三类事件：GATEWAY（用户调用，记录调用方 IP）、UPSTREAM_PROXY（代理请求源 API）、UPSTREAM_TRANSMISSION（中转定时拉取源 API）；统计只算 GATEWAY，避免重复计数，IP 兼容 Caddy 反代注入。
- **Proxy API**：实时转发，支持占位符、参数/Header/Cookie/Body 合并、API Key 鉴权（X-API-Key / Bearer）、超时重试、限流、响应原样透传。
- **Transmission API**：按天/周+指定时间定时拉取源 API，留存全部历史，通过 Data Mapping 字段拖拽生成映射表，调用时返回最新缓存数据。

## 配置文件

所有配置集中在项目根目录的 `config.ini`，**不需要设置环境变量**。首次运行若没有该文件，会自动从 `config.ini.example` 复制一份。`config.ini` 含密钥等敏感信息，已加入 `.gitignore`，请勿提交。

配置分组：

| 分组 | 键 | 默认 | 说明 |
| --- | --- | --- | --- |
| server | host / port / debug | `0.0.0.0` / `5099` / `0` | 开发服务器监听与调试开关 |
| gunicorn | bind | `127.0.0.1:8080` | 生产服务器监听，Caddy 反代到此 |
| gunicorn | workers / threads | `2` / `4` | worker / 线程数，可安全开多 worker |
| gunicorn | accesslog / errorlog / loglevel | `-` / `-` / `info` | 日志 |
| gateway | prefix | `split` | 网关路径模式：`split`=Proxy 挂 `/p/`、Transmission 挂 `/t/`；`gate`/`none` 为旧模式 |
| webauthn | rp_id | `localhost` | 通行密钥 RP ID，部署设为域名 |
| webauthn | origin | `http://localhost:5099` | 通行密钥 Origin，部署设为 `https://域名` |
| security | secret_key | 空（随机） | 会话签名密钥，生产务必设为长随机串 |
| security | cookie_secure | `false` | HTTPS 部署设为 `true` |
| admin | email | `miao@apimiao.local` | 默认 ADMIN 邮箱 |

> 应急覆盖：如需临时覆盖某项而不改文件，可用环境变量 `APIMIAO_<分组>_<键>`，例如 `APIMIAO_WEBAUTHN_RP_ID=example.com`。优先级：环境变量 > config.ini > 模板默认值。

WebAuthn 要求**安全上下文**：本地 `localhost` 可直接测试；部署到服务器必须走 HTTPS 反代（见下方 Caddy 方案），否则浏览器会禁用通行密钥。

## 生产部署（gunicorn + Caddy + systemd）

生产环境用**三个进程**：

- `caddy`：反向代理，**自动申请并续期 HTTPS 证书**（含 HTTP→HTTPS 跳转），无需 certbot。
- `apimiao.service`：gunicorn Web 服务，默认 2 个 worker，只处理 HTTP 请求。
- `apimiao-scheduler.service`：独立的 Transmission 定时任务进程，保证任务只跑一份。

Web 进程**不要**跑 APScheduler——否则每个 worker 都会执行一次定时任务。限流计数存 SQLite，多 worker 共享，不会因进程数放大配额。

### 部署步骤

前提：域名已解析到本机，且 80/443 端口可从公网访问（Caddy 申请证书需要）。

1. 一键安装 Python 依赖、生成 config.ini、注册两个 systemd 服务：

   ```bash
   bash deploy/install.sh
   ```

2. 编辑 `config.ini`：把 `webauthn.rp_id`、`webauthn.origin` 改成你的域名，`security.secret_key` 设为一长串随机串（`openssl rand -hex 32`），`security.cookie_secure=true`。

3. 安装 Caddy 并自动生成 HTTPS 配置（Caddy 会自动申请/续期证书，`EMAIL` 用于 Let's Encrypt 证书账户与到期提醒）：

   ```bash
   DOMAIN=your-domain.com EMAIL=you@example.com bash deploy/caddy-setup.sh
   ```

4. 改完配置后重启两个服务：

   ```bash
   sudo systemctl restart apimiao apimiao-scheduler
   ```

Caddyfile 模板在 `deploy/Caddyfile.example`，改域名后可单独 `sudo caddy validate --config /etc/caddy/Caddyfile` 校验、`sudo systemctl reload caddy` 重载。证书状态看 `journalctl -u caddy -f`。

### 手动启动（不装 systemd，开三个终端）

```bash
# 终端 1：Web（可多 worker）
.venv/bin/gunicorn -c gunicorn.conf.py wsgi:app

# 终端 2：定时任务（只跑一份）
.venv/bin/python scheduler_worker.py

# 终端 3：Caddy 反代（本地开发可用 localhost；正式域名会自动签证书）
caddy run --config deploy/Caddyfile.example
```

> 开发时 `python3 main.py` 会把调度器内嵌在单进程里（已关闭 reloader 避免双启动），方便本地测试；生产务必用上面的多进程方案。

### 故障排查

- **日志出现 `Control server error: Permission denied: '/var/www'`**：这是 gunicorn 25.1+ 的可选运行时控制套接字，默认想在服务用户家目录（www-data 的 home 常为 `/var/www`）建 socket，无写权限所致。**不影响服务运行**。本项目 `gunicorn.conf.py` 已默认禁用它（`config.ini` 的 `gunicorn.control_socket_disable = true`）。若你仍看到此报错，确认已用最新的 `gunicorn.conf.py` 并重启服务；若确实需要 `gunicornc`，改为 `false` 并设置一个服务用户可写的 `control_socket` 路径。
- **`Address already in use` / 端口被占**：macOS 上 5000 端口常被 AirPlay 占用；服务器改端口请编辑 `config.ini` 的 `server.port`（gunicorn 用 `gunicorn.bind`）。
- **`Passkey registration failed: Invalid response`**：本项目的通行密钥代码兼容 webauthn 1.x / 2.x。若报此错，① 确认 `config.ini` 的 `webauthn.rp_id` 是浏览器地址栏域名、`webauthn.origin` 是 `https://该域名`（不带尾斜杠），且用 HTTPS 访问；② 确认服务器用的是最新版 `app/auth/routes.py`（旧版在 webauthn 2.x 下会因 API 差异返回 500，前端即显示此错）；③ 换一个认证器或浏览器重试。

## 项目结构

```
api-miao/
├── main.py                 开发入口（python3 main.py，内嵌调度器）
├── wsgi.py                 WSGI 入口（gunicorn 用，纯 Web 无调度器）
├── scheduler_worker.py     定时任务独立进程入口
├── gunicorn.conf.py        gunicorn 配置（读 config.ini）
├── config.py               配置加载器（读 config.ini）
├── config.ini.example      配置文件模板（复制为 config.ini 使用）
├── config.ini              实际配置（含密钥，gitignore，自动生成）
├── deploy/                 Ubuntu 部署脚本（两个 service）+ Caddy 反代与自动证书
├── requirements.txt
├── app/
│   ├── auth/               登录、2FA、通行密钥、用户管理
│   ├── control/            控制台、API Keys、日志
│   ├── api/                API Manager、Proxy 引擎、Transmission、调度器
│   ├── db.py               SQLite 存储
│   ├── i18n.py             双语词典
│   └── utils.py            工具函数
├── templates/              Jinja2 模板
├── static/                 CSS / JS
└── data/                   SQLite 数据库与初始密码
```

## API 调用示例

网关路径由 `config.ini` 的 `gateway.prefix` 决定，默认 `split` 模式——**两类 API 分路径挂载**：

- Proxy API：`https://域名/p/<你的Endpoint>`
- Transmission API：`https://域名/t/<你的Endpoint>`

API 管理列表里会按此显示完整路径（如 Proxy 的 `h` 显示为 `p/h`），旁边有复制按钮 ⧉ 一键复制。

两种鉴权方式都支持：

```bash
# Proxy API（Endpoint=h，X-API-Key）
curl -H "X-API-Key: <your-key>" https://域名/p/h

# Transmission API（Endpoint=weather，Bearer）
curl -H "Authorization: Bearer <your-key>" https://域名/t/weather
```

旧模式（如需兼容）：`prefix = gate` → `https://域名/gate/<Endpoint>` 不区分类型；`prefix = none` → `https://域名/<Endpoint>` 不区分类型。

> `split` 模式下 `/p/`、`/t/` 与控制台页面路由不会冲突。仅旧 `none` 模式需注意：Endpoint 名不要与 `login`、`logout`、`lang`、`panel/*`、`api/*` 等保留路径重名。任何时候同一用户下「方法 + Endpoint」组合都是唯一匹配条件。

## 存储

SQLite（WAL 模式），数据在 `data/apimiao.db`。库表：users / passkeys / recovery_codes / api_keys / apis / request_logs / api_test_params / transmission_data。
