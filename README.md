# AutoClaw Auto-Login

OpenAI-compatible reverse proxy + Google OAuth auto-login automation for AutoGLM/Z.ai (AutoClaw backend).

Uses [CloakBrowser](https://cloakbrowser.dev) — C++ source-level stealth Chromium (58 patches) instead of raw Playwright. Passes Cloudflare, reCAPTCHA v3, FingerprintJS, BrowserScan without JS injection.

## Features

- **Auto-Login**: Automated Google OAuth login for AutoClaw accounts (batch mode, concurrent)
- **OpenAI-compatible Proxy**: Drop-in `/v1/chat/completions` endpoint — works with any OpenAI client
- **Token Management**: Auto-refresh (24h TTL), round-robin rotation, wallet balance monitoring
- **Dashboard**: Web UI for monitoring accounts, credits, token expiry
- **Stealth**: CloakBrowser handles all fingerprinting at C++ binary level — no JS injection needed

## Quick Start

```bash
# 1. Install dependencies + CloakBrowser binary
pip install -r requirements.txt
python -m cloakbrowser install

# 2. Copy account template
cp accounts.txt.example accounts.txt
# Edit accounts.txt — add email:password per line

# 3. Start proxy (also starts OAuth callback server on port 18432)
python proxy.py

# 4. Auto-login accounts (Google OAuth automation)
python autoclaw_autologin.py --batch accounts.txt --interactive
```

Or on Windows: double-click `setup.bat`, then `start-proxy.bat`, then `run-batch.bat`.

## What Changed from Original (Playwright → CloakBrowser)

- `from playwright.async_api import async_playwright` → `from cloakbrowser import launch_async`
- `p.chromium.launch(headless=..., args=[...stealth flags...])` → `launch_async(headless=...)`
- Removed 15-line `add_init_script()` JS injection (navigator.webdriver, plugins, chrome, WebGL spoofing) — CloakBrowser handles all stealth at C++ binary level
- Removed manual `user_agent=` override — CloakBrowser auto-generates real Chrome UA
- Removed 10+ `--disable-*` args — CloakBrowser has its own default stealth args
- `humanize=True` enabled — human-like mouse, keyboard, and scroll behavior
- Login Success detection — page content checked for "Login Success" to instantly grab token and close browser (no 90s timeout wait)
- Password field error handling — gracefully handles DOM changes during Google's multi-step redirect flow
- Faster timings: initial sleep 2→1s, callback wait 6→0s, post-password 3→1s, consent 1.5→0.5s, loop 1→0.5s
- Typical login time: ~37s per account (was ~121s)

All other files (proxy.py, auth.py, config.py, login.py) unchanged — CloakBrowser returns a standard Playwright Browser object.

## Usage

### Auto-Login (Batch)

```bash
# Interactive — asks headless/concurrent, shows summary
python autoclaw_autologin.py --batch accounts.txt --interactive

# Headless batch with 3 concurrent
python autoclaw_autologin.py --batch accounts.txt --headless --concurrent 3

# Test single account (no save)
python autoclaw_autologin.py --test email@gmail.com:password

# Force re-login
python autoclaw_autologin.py --batch accounts.txt --force
```

Account format in accounts.txt: `email:password` (one per line, # for comments)

### Interactive Login

```bash
# Opens callback server on port 18432
python login.py

# Manual — paste callback URL
python login.py --manual

# List accounts
python login.py --list

# Force refresh all tokens
python login.py --refresh

# Check profile + wallet
python login.py --check
```

### Proxy API

Proxy runs on `http://localhost:31000`. OpenAI-compatible:

```bash
curl http://localhost:31000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "glm-5.2",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Models

| Model alias | Upstream | Note |
|-------------|----------|------|
| `glm-5.2` | `openrouter_glm-5.2` | **Best** — real GLM-5.2 |
| `glm-5.2-true` | `openrouter_glm-5.2` | Same as above |
| `glm-5-turbo` | `zai_glm-5-turbo` | **Cheapest** (-1pt/call) |
| `cheap` | `zai_glm-5-turbo` | Same as above |
| `auto` | `zai_auto` | **Avoid** — secretly DeepSeek ~7x cost |
| `deepseek` | `zai_auto` | Same as above |

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat/completions` | POST | OpenAI chat completions |
| `/v1/models` | GET | List models |
| `/health` | GET | Health check |
| `/accounts` | GET | List stored accounts |
| `/refresh-all` | POST | Force refresh all tokens |
| `/wallet` | GET | Check wallet balance |
| `/ledger` | GET | Check billing ledger |
| `/api/login-url` | POST | Get OAuth URL for browser automation |
| `/auth/callback-google` | GET | OAuth callback handler (auto-captures code) |
| `/api/login-status` | GET | Check if OAuth login completed |
| `/api/accounts-detail` | GET | Accounts with wallet + token expiry |
| `/api/wallet/<email>` | GET | Wallet balance for single account |
| `/api/refresh/<email>` | POST | Refresh token for single account |
| `/api/delete/<email>` | DELETE | Remove account |

## Token Management

- Access token TTL: 24h (auto-refresh 5min before expiry)
- Refresh token TTL: ~30 days
- Auto round-robin across multiple accounts
- Tokens stored in `tokens.json` (gitignored)

## CloakBrowser Notes

- Binary auto-downloads on first run (~535MB, cached at `~/.cloakbrowser/`)
- Free tier: Chromium 146 (58 patches, unlimited sessions)
- Pro tier: Chromium 148 (59 patches, latest anti-bot patches)
- No `playwright install chromium` needed — CloakBrowser has its own binary
- System deps still needed: `python -m playwright install-deps chromium`
- Stealth is automatic — no JS injection, no config, no flags needed

## Files

```
autoclaw-autologin/
├── config.py              # Constants, endpoints, model map
├── auth.py                # Token management, refresh, validation
├── proxy.py               # Flask proxy server + OAuth callback + Dashboard API
├── login.py               # Interactive OAuth login helper
├── autoclaw_autologin.py  # Batch auto-login (CloakBrowser)
├── tokens.json            # Token storage (auto-generated, gitignored)
├── accounts.txt           # email:password list (gitignored)
├── accounts.txt.example   # Template for accounts.txt
├── requirements.txt       # Python deps (cloakbrowser, flask, requests, aiohttp)
├── setup.bat              # First-time setup (installs cloakbrowser + binary)
├── start-proxy.bat        # Start proxy server
├── run-batch.bat          # Batch login (interactive)
├── run-test.bat           # Test single account
├── run.bat                # Quick proxy launcher
├── autoclaw-login.bat     # Login shortcut
├── ui/                    # Dashboard UI
│   └── index.html
└── README.md
```

## Requirements

- Python 3.10+
- Windows (bat scripts) or any OS with Python
- CloakBrowser (auto-installed via `setup.bat` or `python -m cloakbrowser install`)

## License

MIT
