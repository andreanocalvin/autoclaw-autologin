"""AutoClaw Proxy — Constants & Config"""

import os

# ── App Signing ──
APP_ID = "100003"
APP_KEY = "38d2391985e2369a5fb8227d8e6cd5e5"
PRODUCT = "autoclaw"
VERSION = "1.9.1"
PLATFORM = "win"

# ── Endpoints ──
USER_API_BASE = "https://autoglm-api.autoglm.ai"
LLM_PROXY_BASE = "https://autoglm-api.autoglm.ai/autoclaw-proxy/proxy/autoclaw"
CHAT_COMPLETIONS = f"{LLM_PROXY_BASE}/chat/completions"

# ── Auth Endpoints ──
GOOGLE_OAUTH_URL = f"{USER_API_BASE}/userapi/overseasv1/google-oauth-url"
GOOGLE_OAUTH_LOGIN = f"{USER_API_BASE}/userapi/overseasv1/google-oauth-login"
REFRESH_URL = f"{USER_API_BASE}/userapi/v1/refresh"
PROFILE_URL = f"{USER_API_BASE}/userapi/v1/user-profile"
WALLET_URL = f"{USER_API_BASE}/agent-assetmgr/api/v2/wallets?biz_app_id=autoclaw"
LEDGER_URL = f"{USER_API_BASE}/agent-assetmgr/api/v1/ledgers_std?asset_type=point&wallet_type=all"

# ── Token Storage ──
TOKENS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tokens.json")

# ── Proxy Server ──
PROXY_HOST = "0.0.0.0"
PROXY_PORT = 31000

# ── Model Map (X-Request-Model → alias) ──
# Key = what client sends as "model" in OpenAI body
# Value = X-Request-Model header value sent to AutoClaw upstream
MODEL_MAP = {
    # Best — real GLM-5.2
    "glm-5.2": "openrouter_glm-5.2",
    "glm-5.2-true": "openrouter_glm-5.2",
    # Cheapest — glm-5-turbo
    "glm-5-turbo": "zai_glm-5-turbo",
    "cheap": "zai_glm-5-turbo",
    # Avoid — secretly DeepSeek-V4-Pro ~7x cost
    "auto": "zai_auto",
    "deepseek": "zai_auto",
}

DEFAULT_MODEL = "openrouter_glm-5.2"

# ── Access Token TTL (24h, refresh 5min before expiry) ──
ACCESS_TOKEN_TTL = 86400  # 24h
REFRESH_MARGIN = 300      # 5min before expiry

# ── Billing Header Quirks ──
# LLM proxy: X-Authorization (capital X)
# Assetmgr: authorization (lowercase)
