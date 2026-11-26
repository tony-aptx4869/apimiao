# APIMiao

A self-hosted API relay platform. Configure how an upstream API is called once, then call it through this platform — bypassing rate limits, free-tier quotas, and IP allow/deny lists.

MIT licensed. See [LICENSE](LICENSE).

**Language:** [English](README.md) · [中文](README_zh.md)

## Quick start

```bash
cd api-miao
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 main.py
```

On first run, if `config.ini` is missing, it is created from `config.ini.example` with working defaults. Change the port, domain, or secrets by editing `config.ini`. **No environment variables are required.**

Open <http://localhost:5099>.

The first run creates a default ADMIN account `miao@apimiao.local` (ID `0000005f`). The initial password is printed to the terminal and written to `data/initial_password.txt`. You are forced to change it on first login — delete that file afterward.

To reset a forgotten password (generates a new random password and writes it to `data/initial_password.txt`):

```bash
python3 -c "from app.db import reset_admin_password; print(reset_admin_password())"
```

## Features

- **Bilingual UI.** Switch English / 汉语 from the Language menu. The choice is stored in a cookie, not the URL. Dates render in the browser's local timezone while the database stores UTC.
- **Login.** Email + password, with optional TOTP 2FA (Google Authenticator, etc.), or passkeys (WebAuthn).
- **Passkeys.** Platform is derived from the registration AAGUID against a maintained registry; unknown authenticators are labelled `Other` with an in-app explanation. Passkeys support discoverable login without an email, and can be renamed.
- **2FA recovery codes.** Enabling 2FA generates 10 one-time recovery codes (stored hashed, shown once). Use them if you lose your authenticator. Check the remaining count or regenerate them in Profile.
- **Roles.** ADMIN manages all users; USER can only edit their own profile.
- **API keys.** UUID format. A newly created key is shown in full once in a centered modal (dimmed background, manual close); afterward only the first and last 4 characters are visible.
- **Control Panel.** Overview (usage over the last 1h / 24h / 7d / 30d) and Logs (30-day stats, daily line chart, top 9 endpoints, filters, call history). Logs distinguish three event types: `GATEWAY` (user calls, records caller IP), `UPSTREAM_PROXY` (proxy calls to upstream), and `UPSTREAM_TRANSMISSION` (scheduled pulls). Only `GATEWAY` counts toward stats, avoiding double counting. IPs work behind Caddy reverse proxy.
- **Proxy API.** Live forwarding with placeholders, parameter/header/cookie/body merging, API-key auth (`X-API-Key` / Bearer), timeout and retry, rate limiting, and raw response passthrough.
- **Transmission API.** Scheduled pulls by day/week and time, keeping full history. Field mapping is built by dragging fields in Data Mapping; calls return the latest cached data.

## Configuration

All configuration lives in `config.ini` at the project root. **No environment variables are needed.** On first run the file is copied from `config.ini.example`. `config.ini` contains secrets and is gitignored — do not commit it.

| Section | Keys | Default | Purpose |
| --- | --- | --- | --- |
| general | timezone | `Asia/Shanghai` | IANA timezone for daily stats grouping; DB always stores UTC |
| server | host / port / debug | `0.0.0.0` / `5099` / `0` | Dev server bind address and debug flag |
| server | egress_ip | empty (auto-detected) | Public egress IP as seen by upstream APIs |
| server | trusted_proxies | `127.0.0.1,::1` | Comma-separated proxies whose forwarding headers are trusted |
| gunicorn | bind | `127.0.0.1:8080` | Production bind address, reverse-proxied by Caddy |
| gunicorn | workers / threads | `2` / `4` | Worker and thread counts (safe to use multiple workers) |
| gunicorn | accesslog / errorlog / loglevel | `-` / `-` / `info` | Logging |
| gunicorn | control_socket_disable / control_socket | `true` / empty | Gunicorn runtime control socket (disabled by default) |
| gateway | prefix | `split` | URL path mode: `split` = Proxy at `/p/`, Transmission at `/t/`; `gate` / `none` are legacy |
| webauthn | rp_id / rp_name / origin | `localhost` / `APIMiao` / `http://localhost:5099` | Passkey RP settings; use your domain in production |
| security | secret_key | empty (generated) | Session signing key; set a long random string in production |
| security | cookie_secure | `false` | Set `true` for HTTPS deployments |
| admin | email | `miao@apimiao.local` | Default ADMIN email (fixed ID `0000005f`) |
| trans | cartesian_limit | `10000` | Cap on Origin-list Cartesian combinations; `0` or negative disables the limit |
| trans | row_retention_days / log_retention_days | `0` / `0` | Snapshot and collection-log retention in days; `0` means no cleanup |

> Emergency override: to temporarily override a value without editing the file, use `APIMIAO_<SECTION>_<KEY>`, for example `APIMIAO_WEBAUTHN_RP_ID=example.com`. Precedence: environment variable > `config.ini` > template default.

WebAuthn requires a **secure context**. `localhost` works for local testing; a deployed server must use an HTTPS reverse proxy (see Caddy below), or the browser will disable passkeys.

## Production deployment (gunicorn + Caddy + systemd)

Production uses **three processes**:

- `caddy`: reverse proxy that **automatically obtains and renews HTTPS certificates** (including HTTP→HTTPS redirect). No certbot needed.
- `apimiao.service`: gunicorn web service, 2 workers by default, handles HTTP requests only.
- `apimiao-scheduler.service`: separate Transmission scheduler process, so scheduled jobs run exactly once.

Do **not** run APScheduler in the web process — otherwise every worker would run each scheduled job. Rate-limit counters live in SQLite and are shared across workers, so quotas do not multiply with worker count.

### Steps

Prerequisites: the domain resolves to this host, and ports 80/443 are publicly reachable (Caddy needs them to obtain a certificate).

1. Install Python dependencies, generate `config.ini`, and register the two systemd services:

   ```bash
   bash deploy/install.sh
   ```

2. Edit `config.ini`: set `webauthn.rp_id` and `webauthn.origin` to your domain, set `security.secret_key` to a long random string (`openssl rand -hex 32`), and set `security.cookie_secure=true`.

3. Install Caddy and generate HTTPS config automatically (Caddy obtains/renews certificates; `EMAIL` is used for the Let's Encrypt account and expiry notices):

   ```bash
   DOMAIN=your-domain.com EMAIL=you@example.com bash deploy/caddy-setup.sh
   ```

4. Restart both services after changing config:

   ```bash
   sudo systemctl restart apimiao apimiao-scheduler
   ```

The Caddyfile template is `deploy/Caddyfile.example`. After editing the domain you can validate with `sudo caddy validate --config /etc/caddy/Caddyfile` and reload with `sudo systemctl reload caddy`. Check certificate status with `journalctl -u caddy -f`.

### Manual start (no systemd, three terminals)

```bash
# Terminal 1: web (multiple workers OK)
.venv/bin/gunicorn -c gunicorn.conf.py wsgi:app

# Terminal 2: scheduler (exactly one process)
.venv/bin/python scheduler_worker.py

# Terminal 3: Caddy reverse proxy (localhost works for dev; a real domain gets a certificate automatically)
caddy run --config deploy/Caddyfile.example
```

> In development, `python3 main.py` embeds the scheduler in a single process (the reloader is disabled to avoid double starts). For production, always use the multi-process setup above.

### Troubleshooting

- **`Control server error: Permission denied: '/var/www'`** — an optional gunicorn 25.1+ runtime control socket that defaults to the service user's home (`www-data` often has home `/var/www` without write access). It does not affect the service. `gunicorn.conf.py` disables it by default (`gunicorn.control_socket_disable = true`). If you still see it, ensure you are using the latest `gunicorn.conf.py` and restart. If you need `gunicornc`, set it to `false` and give `control_socket` a path writable by the service user.
- **`Address already in use` / port occupied** — on macOS port 5000 is often taken by AirPlay. On the server change `server.port` in `config.ini` (gunicorn uses `gunicorn.bind`).
- **`Passkey registration failed: Invalid response`** — the passkey code supports webauthn 1.x / 2.x. If this appears: (1) confirm `webauthn.rp_id` equals the domain in the browser address bar and `webauthn.origin` is `https://that-domain` with no trailing slash, accessed over HTTPS; (2) confirm the server runs the latest `app/auth/routes.py` (older versions fail under webauthn 2.x due to API differences); (3) try another authenticator or browser.

## Project structure

```
api-miao/
├── main.py                  Dev entry (python3 main.py, scheduler embedded)
├── wsgi.py                  WSGI entry for gunicorn (web only, no scheduler)
├── scheduler_worker.py      Standalone scheduler process for Transmission
├── gunicorn.conf.py         gunicorn config (reads config.ini)
├── config.py                Config loader (reads config.ini)
├── config.ini.example       Config template (copy to config.ini)
├── config.ini               Active config (secrets, gitignored, auto-generated)
├── deploy/                  Ubuntu deploy scripts (two services) + Caddy reverse proxy and certificates
├── requirements.txt
├── app/
│   ├── auth/                Login, 2FA, passkeys, user management
│   ├── control/             Control panel, API keys, logs
│   ├── api/                 API manager, proxy engine, Transmission, scheduler
│   ├── data_sheets/         Data sheet discovery, fields, and snapshots
│   ├── db.py                SQLite storage
│   ├── i18n.py              Bilingual dictionary
│   └── utils.py             Utilities
├── templates/               Jinja2 templates
├── static/                  CSS / JS
└── data/                    SQLite database and initial password
```

## API call examples

Gateway paths are set by `gateway.prefix` in `config.ini`. The default `split` mode mounts the two API types at different paths:

- Proxy API: `https://<domain>/p/<your-endpoint>`
- Transmission API: `https://<domain>/t/<your-endpoint>`

The API management list shows the full path (e.g. Proxy `h` appears as `p/h`) with a copy button.

Both auth methods are supported:

```bash
# Proxy API (endpoint h, X-API-Key)
curl -H "X-API-Key: <your-key>" https://<domain>/p/h

# Transmission API (endpoint weather, Bearer)
curl -H "Authorization: Bearer <your-key>" https://<domain>/t/weather
```

Legacy modes: `prefix = gate` → `https://<domain>/gate/<endpoint>` for both types; `prefix = none` → `https://<domain>/<endpoint>` for both.

> In `split` mode `/p/` and `/t/` do not collide with control-panel routes. Only in legacy `none` mode must endpoint names avoid reserved paths such as `login`, `logout`, `lang`, `panel/*`, `api/*`. In all modes the unique match is the `(method, endpoint)` pair per user.

## Storage

SQLite in WAL mode, at `data/apimiao.db`. Core tables: users / passkeys / recovery_codes / api_keys / apis / request_logs / api_test_params / trans_apis / trans_origins / data_sheets / sheet_fields / data_rows / api_cache / fetch_batches / fetch_logs / precheck_tasks / rate_limit_hits / schema_migrations. Older databases may still contain a legacy `transmission_data` table.
