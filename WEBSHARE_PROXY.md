# Webshare Proxy Automation — Bypass 630014 Rate Limit

## Problem

Z.ai's `zai-oauth-login` endpoint enforces a rate limit of ~1-2 account registrations per IP per cooldown period. After that, you get `630014` ("审核失败,请重新尝试"). This makes batch account creation impossible from a single IP.

## Solution

Use **Webshare rotating proxies** — each account gets a different IP, bypassing the rate limit entirely.

### Results

- **98/100 success rate (98%)** with 100 accounts + 100 proxies
- ~1 minute per account (sequential, clean browser per account)
- Zero `630014` errors when using rotating IPs

## Setup

### 1. Install CloakBrowser

```bash
pip install cloakbrowser
```

CloakBrowser is an anti-detect browser (real Chrome UA, no headless detection) that works as a drop-in Playwright replacement.

### 2. Get Webshare Proxies

Sign up at [webshare.io](https://webshare.io) and create a proxy list. You need at least 100 IPs for a 100-account batch.

### 3. Configure

```bash
cp .env.example .env
# Edit .env — set CLAW_PROXY_DB to your proxy SQLite DB path
```

Or use a proxy pool DB (PoolProx2 format):

```sql
CREATE TABLE proxy_pool (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,          -- http://user:pass@host:port
    type TEXT DEFAULT 'http',
    status TEXT DEFAULT 'active'
);
```

### 4. Create Accounts File

```bash
cp accounts.txt.example accounts.txt
# Edit with your email:password lines (one per line)
```

### 5. Run

```bash
# Test with one account
python claw_single.py email@domain.com password 0

# Batch
python claw_batch.py accounts.txt
```

## Flow

```
1. CloakBrowser (headless, UA Chrome 146) + webshare proxy (different IP per account)
2. Google OAuth via Z.ai (chat.z.ai/oauth/google/login)
   - Fill email → password → consent "Continue"
3. Z.ai session established
4. Navigate to autoclaw.z.ai/web → click "去注册" → click "Continue with Zai"
5. Popup: Z.ai OAuth consent page
   - Check Terms checkbox → click "Continue"
6. Redirect back to AutoClaw → zai-oauth-login API (200 OK)
7. Security agreement popup → scroll to bottom → click "我已阅读并同意"
8. Token appears in localStorage → save to tokens.json
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAW_PROXY_DB` | (empty) | Path to SQLite DB with proxy pool |
| `CLAW_TOKENS_FILE` | `tokens.json` | Output file for tokens |
| `CLAW_LOG_FILE` | `/tmp/claw_batch.log` | Log file path |

## Files

| File | Description |
|------|-------------|
| `claw_batch.py` | Batch automation script — N accounts with N proxies |
| `claw_single.py` | Single account test — good for debugging |
| `.env.example` | Environment variable template |

## Proxy Patch for proxy.py

Also includes patches to `proxy.py`:
- **Timeout**: 120s → 600s (long responses)
- **Tool calls streaming**: properly accumulate and forward `tool_calls` in stream mode
- **Reasoning strip**: remove `reasoning` / `reasoning_details` fields (Cline/OpenAI clients don't understand them)
- **Empty chunk skip**: skip pure-thinking chunks with no content/tool_calls
