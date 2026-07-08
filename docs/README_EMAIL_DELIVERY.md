# Email Delivery (Gmail + Outlook)

## What is this?

All daily agents send HTML email through a shared pipeline in `daily_email_send.py`:

1. **Primary provider** — usually Gmail SMTP (`EMAIL_SEND_PROVIDER=gmail`)
2. **Automatic fallback** — Outlook via Microsoft Graph (`EMAIL_SEND_FALLBACK_PROVIDER=outlook`)

This design handles corporate networks where Gmail SMTP (`smtp.gmail.com:587`) is blocked.

```
send_html_email()
        │
        ├─ gmail ──► gmail_send.py (SMTP + App Password)
        │     │
        │     └─ fail (timeout / auth)
        │
        └─ outlook ──► subprocess → outlook_send_helper.py
                              │
                              ▼
                       Outlook MCP v4 (Graph API)
```

---

## Gmail setup

### 1. Enable 2FA on Google account

### 2. Create App Password

Google Account → Security → App passwords → generate for “Mail”.

### 3. Configure `.env`

```env
EMAIL_SEND_PROVIDER=gmail
EMAIL_SEND_FALLBACK_PROVIDER=outlook

GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
GMAIL_TO=you@gmail.com,other@gmail.com
GMAIL_SELF_ONLY=1
```

| Variable | Description |
|----------|-------------|
| `GMAIL_SELF_ONLY=1` | Default sends only to `GMAIL_TO` (ignores agent recipient unless overridden) |
| `GMAIL_SELF_ONLY=0` | Agent can set explicit TO/BCC (used for multi-profile) |

### Common Gmail errors

| Error | Cause | Fix |
|-------|-------|-----|
| `WinError 10060` | SMTP blocked / timeout | Outlook fallback (or use VPN) |
| `Authentication failed` | Wrong app password | Regenerate app password |
| `GMAIL_APP_PASSWORD is not set` | Missing `.env` | Add password |

---

## Outlook setup

### 1. Install Outlook MCP

Set path in `.env`:

```env
OUTLOOK_MCP_DIR=C:\path\to\outlook-mcp-server-v4
```

If unset, the code auto-detects `C:\amdocs\mcp-servers\outlook-mcp-server-v4` when that folder exists.

### 2. Path resolution (`outlook_mcp_env.py`)

**Important:** `outlook_send_helper.py` loads `.env` **before** resolving the MCP path. Scheduled tasks inherit environment from the agent process.

### 3. Authenticate (one-time)

```powershell
powershell -ExecutionPolicy Bypass -File start_outlook_auth_server.ps1
```

Open `http://localhost:8081/signin` and sign in with Microsoft.

Register logon task (keeps auth server available):

```powershell
powershell -ExecutionPolicy Bypass -File setup_outlook_auth_at_logon.ps1
```

### 4. Test Outlook send

```powershell
uv run python daily_tech_news_email_agent.py --no-retry
```

---

## Provider selection

```env
# Gmail first, Outlook on failure (recommended on corp network)
EMAIL_SEND_PROVIDER=gmail
EMAIL_SEND_FALLBACK_PROVIDER=outlook

# Outlook only
EMAIL_SEND_PROVIDER=outlook
EMAIL_SEND_FALLBACK_PROVIDER=

# Gmail only (no fallback)
EMAIL_SEND_PROVIDER=gmail
EMAIL_SEND_FALLBACK_PROVIDER=
```

---

## Scheduled task behavior

- `configure_scheduled_outlook_env()` sets `OUTLOOK_SEND_NON_INTERACTIVE=1` when no TTY
- Scheduled sends **must not** open a browser — tokens must already be cached
- Retry: 3 attempts, 10 minutes apart (`DAILY_EMAIL_RETRY_DELAY_SECONDS=600`)

---

## Multi-recipient sends

Job search profiles pass explicit `to_recipients` and `bcc_recipients` to `send_html_email()`, bypassing `GMAIL_SELF_ONLY` when lists are provided.

---

## Key files

| File | Role |
|------|------|
| `daily_email_send.py` | Provider routing, retry, Outlook subprocess |
| `gmail_send.py` | SMTP HTML send |
| `outlook_send_helper.py` | CLI entry for Outlook MCP venv |
| `outlook_mcp_env.py` | MCP path resolution |
| `start_outlook_auth_server.ps1` | OAuth helper server |

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'outlook_client'`

- `OUTLOOK_MCP_DIR` wrong or `.env` not loaded before path resolution
- Fix: set `OUTLOOK_MCP_DIR` in `.env`; ensure latest `outlook_mcp_env.py` is present

### `Outlook MCP Python not found`

Path: `<OUTLOOK_MCP_DIR>\.venv\Scripts\python.exe` must exist.

### Send works manually but not from Task Scheduler

- PC must be logged in (`LogonType Interactive`)
- Run Outlook auth at logon task
- Check `logs/*.log` for the failed attempt

---

## Security notes

- Never commit Gmail app passwords or Graph secrets
- `.env` is gitignored
- Outlook tokens stored in MCP project’s token store
