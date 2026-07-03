"""AutoClaw Proxy — OpenAI-compatible reverse proxy for AutoGLM/Z.ai

Endpoints:
  POST /v1/chat/completions   — OpenAI chat completions (stream + non-stream)
  GET  /v1/models             — list available models
  GET  /health                — health check
  GET  /accounts              — list stored accounts
  POST /refresh-all           — force refresh all tokens
  GET  /wallet                — check wallet balance
  GET  /ledger                — check billing ledger
  GET  /auth/callback-google  — OAuth callback handler (auto-captures code)
"""

import json
import time
import uuid
import threading
from flask import Flask, request, Response, jsonify
import requests as req_lib
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import (
    CHAT_COMPLETIONS, MODEL_MAP, DEFAULT_MODEL,
    PROXY_HOST, PROXY_PORT, TOKENS_FILE,
)
from auth import (
    get_valid_token, refresh_all, list_accounts,
    check_wallet, check_ledger, load_tokens,
)

TOKENS_FILE_FULL = TOKENS_FILE  # full path for backup operations

app = Flask(__name__)

# ── Pending OAuth login state (supports concurrent logins) ──
_pending_logins = {}  # keyed by state: {state: {"device_id":..., "result":..., "error":...}}

# ── Round-robin token rotation ──
_token_idx = 0
_token_lock = threading.Lock()

# ── Request counter per account ──
_request_counts = {}  # email → int


def get_next_token():
    """Round-robin token selection across all accounts.
    Auto-refreshes expired tokens. Skips known-exhausted accounts (from cache only)."""
    global _token_idx
    data = load_tokens()
    if not data["accounts"]:
        return None, None

    n = len(data["accounts"])
    with _token_lock:
        idx = _token_idx % n
        _token_idx += 1

    # Try each account starting from idx
    for i in range(n):
        acc = data["accounts"][(idx + i) % n]
        # Check token validity
        from config import ACCESS_TOKEN_TTL, REFRESH_MARGIN
        age = time.time() - acc.get("last_refreshed", 0)
        if age < ACCESS_TOKEN_TTL - REFRESH_MARGIN:
            # Token still valid — check exhausted cache (non-blocking, cached only)
            if not _is_cached_exhausted(acc):
                return acc["access_token"], acc
            else:
                continue
        # Try refresh
        from auth import refresh_token
        new_token = refresh_token(acc)
        if new_token:
            if not _is_cached_exhausted(acc):
                return new_token, acc
            else:
                continue

    # Fallback: return first valid token even if exhausted
    for acc in data["accounts"]:
        return acc["access_token"], acc
    return None, None


# ── Exhausted accounts cache (wallet balance=0) ──
# Only uses cache — does NOT make API calls during chat requests
_exhausted_cache = {}  # email → {"balance": int, "checked": timestamp}
_EXHAUSTED_CACHE_TTL = 300  # cache valid for 5 min


def _is_cached_exhausted(acc):
    """Check if account is known to be exhausted (cache only, no API call)."""
    email = acc.get("email", "")
    now = time.time()
    cached = _exhausted_cache.get(email)
    if cached and (now - cached["checked"]) < _EXHAUSTED_CACHE_TTL:
        return cached["balance"] <= 0
    # No cache or expired — assume NOT exhausted (don't block chat)
    return False


def _refresh_exhausted_cache():
    """Background: check all accounts' wallet balances and update cache.
    Called periodically, not during chat requests."""
    data = load_tokens()
    for acc in data["accounts"]:
        email = acc.get("email", "")
        try:
            result = check_wallet(acc["access_token"])
            if result.get("code") == 0:
                balance = result["data"]["total_balance"]
                _exhausted_cache[email] = {"balance": balance, "checked": time.time()}
                if balance <= 0:
                    print(f"[proxy] Exhausted: {email} (0 pts)")
        except:
            pass


def _bg_wallet_checker():
    """Background thread: refresh exhausted cache every 5 minutes."""
    while True:
        try:
            _refresh_exhausted_cache()
        except Exception as e:
            print(f"[proxy] Wallet checker error: {e}")
        time.sleep(_EXHAUSTED_CACHE_TTL)


def _sign_headers():
    import hashlib
    from config import APP_ID, APP_KEY, PRODUCT, VERSION, PLATFORM
    ts = str(int(time.time()))
    sign = hashlib.md5(f"{APP_ID}&{ts}&{APP_KEY}".encode()).hexdigest()
    return {
        "X-Auth-Appid": APP_ID,
        "X-Auth-TimeStamp": ts,
        "X-Auth-Sign": sign,
        "X-Product": PRODUCT,
        "X-Version": VERSION,
        "X-Tm": PLATFORM,
        "X-Trace-Id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    """OpenAI-compatible chat completions proxy."""
    body = request.get_json(force=True)
    if not body:
        return jsonify({"error": {"message": "Invalid JSON body"}}), 400

    # Model mapping
    client_model = body.get("model", DEFAULT_MODEL)
    upstream_model = MODEL_MAP.get(client_model, DEFAULT_MODEL)

    # Force stream=True for upstream (DeepSeek models 500 on non-stream)
    upstream_body = dict(body)
    upstream_body["stream"] = True
    upstream_body["model"] = "x"  # ignored by upstream, but fill it

    # Get token (round-robin)
    access_token, acc = get_next_token()
    if not access_token:
        return jsonify({
            "error": {
                "message": "No valid tokens. Add accounts first via /login or manually edit tokens.json",
                "type": "auth_error",
            }
        }), 401

    # Increment request counter for this account
    used_email = acc.get("email", "unknown")
    _request_counts[used_email] = _request_counts.get(used_email, 0) + 1

    headers = _sign_headers()
    # Token already has "Bearer " prefix — don't double it
    raw_token = access_token.replace("Bearer ", "")
    headers["X-Authorization"] = f"Bearer {raw_token}"
    headers["X-Request-Id"] = str(uuid.uuid4())
    headers["X-Request-Model"] = upstream_model

    client_wants_stream = body.get("stream", False)

    try:
        # Always request stream from upstream
        upstream_resp = req_lib.post(
            CHAT_COMPLETIONS,
            json=upstream_body,
            headers=headers,
            stream=True,
            timeout=120,
            verify=False,
        )

        if upstream_resp.status_code != 200:
            error_text = upstream_resp.text[:1000]
            return jsonify({
                "error": {
                    "message": f"Upstream error {upstream_resp.status_code}: {error_text}",
                    "type": "upstream_error",
                    "code": upstream_resp.status_code,
                }
            }), upstream_resp.status_code

        if client_wants_stream:
            # Pass through SSE stream — filter non-data lines (e.g. ": OPENROUTER PROCESSING")
            def generate():
                for line in upstream_resp.iter_lines():
                    if line:
                        # Only forward valid SSE data lines
                        if line.startswith(b"data:"):
                            yield line + b"\n\n"
                yield b"data: [DONE]\n\n"
            return Response(
                generate(),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            # Aggregate stream → single JSON response (OpenAI non-stream format)
            full_content = ""
            finish_reason = None
            model_name = upstream_model

            for line in upstream_resp.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8", errors="replace")
                if line_str.startswith("data: "):
                    data_str = line_str[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            if "content" in delta:
                                full_content += delta["content"]
                            if choices[0].get("finish_reason"):
                                finish_reason = choices[0]["finish_reason"]
                        if chunk.get("model"):
                            model_name = chunk["model"]
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                    except json.JSONDecodeError:
                        pass

            response = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": client_model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": full_content},
                    "finish_reason": finish_reason or "stop",
                }],
                "usage": usage if 'usage' in dir() else {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
            return jsonify(response)

    except req_lib.exceptions.Timeout:
        return jsonify({"error": {"message": "Upstream timeout"}}), 504
    except Exception as e:
        return jsonify({"error": {"message": f"Proxy error: {str(e)}"}}), 500


@app.route("/v1/models", methods=["GET"])
def list_models():
    """List available models (OpenAI-compatible)."""
    models = []
    for alias, upstream in MODEL_MAP.items():
        models.append({
            "id": alias,
            "object": "model",
            "created": 1700000000,
            "owned_by": "autoclaw",
            "upstream": upstream,
        })
    return jsonify({"object": "list", "data": models})


@app.route("/health", methods=["GET"])
def health():
    data = load_tokens()
    return jsonify({
        "status": "ok",
        "accounts": len(data["accounts"]),
        "port": PROXY_PORT,
    })


@app.route("/accounts", methods=["GET"])
def accounts():
    """List all stored accounts."""
    list_accounts()
    data = load_tokens()
    safe = []
    for acc in data["accounts"]:
        safe.append({
            "email": acc["email"],
            "user_id": acc.get("user_id"),
            "added_at": acc.get("added_at"),
            "last_refreshed": acc.get("last_refreshed"),
            "token_preview": acc["access_token"][:30] + "..." if acc.get("access_token") else None,
        })
    return jsonify({"accounts": safe})


@app.route("/api/accounts-detail", methods=["GET"])
def accounts_detail():
    """List all accounts with wallet balance + token expiry (bulk)."""
    import base64 as _b64
    data = load_tokens()
    result = []
    for acc in data["accounts"]:
        # Decode JWT for expiry
        exp = 0
        iat = 0
        try:
            token = acc["access_token"].replace("Bearer ", "")
            parts = token.split(".")
            payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
            decoded = json.loads(_b64.urlsafe_b64decode(payload))
            exp = decoded.get("exp", 0)
            iat = decoded.get("iat", 0)
        except:
            pass

        now = time.time()
        remaining_h = max(0, (exp - now) / 3600) if exp else 0
        expired = remaining_h <= 0

        result.append({
            "email": acc["email"],
            "user_id": acc.get("user_id", ""),
            "device_id": acc.get("device_id", ""),
            "added_at": acc.get("added_at", 0),
            "last_refreshed": acc.get("last_refreshed", 0),
            "expires_at": exp,
            "remaining_hours": round(remaining_h, 1),
            "expired": expired,
            "has_refresh_token": bool(acc.get("refresh_token")),
            "request_count": _request_counts.get(acc.get("email", ""), 0),
        })
    return jsonify({"accounts": result, "total": len(result)})


@app.route("/api/refresh/<path:email>", methods=["POST"])
def refresh_single(email):
    """Refresh access token for a single account."""
    from auth import refresh_token as do_refresh
    data = load_tokens()
    acc = None
    for a in data["accounts"]:
        if a.get("email") == email:
            acc = a
            break
    if not acc:
        return jsonify({"error": "Account not found"}), 404

    new_token = do_refresh(acc)
    if new_token:
        return jsonify({"success": True, "email": email, "message": "Token refreshed"})
    return jsonify({"success": False, "email": email, "error": "Refresh failed"}), 500


@app.route("/api/wallet-bulk", methods=["GET"])
def wallet_bulk():
    """Get wallet balance for all accounts (bulk)."""
    data = load_tokens()
    result = []
    for acc in data["accounts"]:
        balance = None
        status = "active"
        try:
            wallet_data = check_wallet(acc["access_token"])
            if wallet_data.get("code") == 0:
                balance = wallet_data["data"]["total_balance"]
                if balance <= 0:
                    status = "exhausted"
        except:
            status = "error"
        result.append({
            "email": acc["email"],
            "balance": balance,
            "status": status,
        })
    return jsonify({"accounts": result})


@app.route("/api/delete/<path:email>", methods=["DELETE"])
def delete_account(email):
    """Remove an account from tokens.json."""
    data = load_tokens()
    before = len(data["accounts"])
    data["accounts"] = [a for a in data["accounts"] if a.get("email") != email]
    after = len(data["accounts"])
    if after < before:
        save_tokens(data)
        return jsonify({"success": True, "email": email})
    return jsonify({"error": "Account not found"}), 404


@app.route("/refresh-all", methods=["POST"])
def do_refresh_all():
    # Backup tokens.json before refresh (preventive)
    import shutil
    try:
        shutil.copy2(TOKENS_FILE_FULL, TOKENS_FILE_FULL + ".bak")
    except Exception as e:
        print(f"[proxy] Backup before refresh-all failed: {e}")
    success, fail = refresh_all()
    return jsonify({"success": success, "fail": fail})


@app.route("/wallet", methods=["GET"])
def wallet():
    """Check wallet balance for first account."""
    data = load_tokens()
    if not data["accounts"]:
        return jsonify({"error": "No accounts"}), 404
    acc = data["accounts"][0]
    result = check_wallet(acc["access_token"])
    return jsonify(result)


@app.route("/ledger", methods=["GET"])
def ledger():
    """Check billing ledger for first account."""
    data = load_tokens()
    if not data["accounts"]:
        return jsonify({"error": "No accounts"}), 404
    acc = data["accounts"][0]
    result = check_ledger(acc["access_token"])
    return jsonify(result)


# ── Web UI ──

import os

from flask import send_from_directory

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


@app.route("/")
def ui_dashboard():
    return send_from_directory(UI_DIR, "index.html")


@app.route("/ui/<path:path>")
def ui_static(path):
    return send_from_directory(UI_DIR, path)


@app.route("/api/login-url", methods=["POST"])
def api_login_url():
    """Generate Google OAuth URL for browser-based login.
    Stores pending state → auto-captured when /auth/callback-google is hit.
    """
    from auth import google_oauth_url
    oauth_url, state, device_id = google_oauth_url()
    if oauth_url:
        _pending_logins[state] = {"device_id": device_id, "result": None, "error": None}
        return jsonify({"oauth_url": oauth_url, "state": state, "device_id": device_id})
    return jsonify({"error": "Failed to get OAuth URL"}), 500


@app.route("/auth/callback-google")
def auth_callback_google():
    """Google OAuth redirect target — auto-captures code+state, exchanges tokens."""
    from auth import google_oauth_login, add_token

    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    # Find pending login by state
    pending = _pending_logins.get(state)
    if not pending:
        # Try to find any pending login (fallback for old-style)
        if _pending_logins:
            # State might not match exactly — find first pending without result
            for s, p in _pending_logins.items():
                if p.get("result") is None and p.get("error") is None:
                    pending = p
                    break
        if not pending:
            return "<html><body><h1>Login Failed</h1><p>Unknown state</p></body></html>", 400

    if error:
        pending["error"] = error
        return f"<html><body><h1>Login Failed</h1><p>{error}</p></body></html>", 400

    if not code:
        pending["error"] = "No code in callback"
        return "<html><body><h1>Login Failed</h1><p>No code</p></body></html>", 400

    device_id = pending.get("device_id")

    result = google_oauth_login(code, state, device_id)
    if not result:
        pending["error"] = "Token exchange failed"
        return "<html><body><h1>Login Failed</h1><p>Token exchange failed</p></body></html>", 500

    # Extract email from JWT jti field
    import base64 as _b64
    email = result.get("user_name") or f"user_{result['user_id']}"
    try:
        token_part = result["access_token"].replace("Bearer ", "").split(".")[1]
        token_part += "=" * (4 - len(token_part) % 4)
        payload = json.loads(_b64.urlsafe_b64decode(token_part))
        if payload.get("jti"):
            email = payload["jti"]
    except:
        pass

    add_token(
        email=email,
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        user_id=result["user_id"],
        device_id=device_id,
    )
    pending["result"] = {
        "email": email,
        "user_id": result.get("user_id"),
        "first_login": result.get("first_login"),
    }
    return f"""<html><body>
<h1>Login Success!</h1>
<p>Email: {email}</p>
<p>User ID: {result.get('user_id')}</p>
<p>You can close this tab.</p>
</body></html>"""


@app.route("/api/login-status", methods=["GET"])
def api_login_status():
    """Check if pending OAuth login completed.
    Query param: state=<state> for concurrent login tracking.
    """
    state = request.args.get("state")

    if state:
        # Specific state lookup (concurrent mode)
        pending = _pending_logins.get(state)
        if not pending:
            return jsonify({"status": "pending"})
        if pending.get("result"):
            return jsonify({"status": "ok", "account": pending["result"]})
        if pending.get("error"):
            return jsonify({"status": "error", "error": pending["error"]})
        return jsonify({"status": "pending"})
    else:
        # Legacy: return first available result
        for s, p in _pending_logins.items():
            if p.get("result"):
                return jsonify({"status": "ok", "account": p["result"]})
            if p.get("error"):
                return jsonify({"status": "error", "error": p["error"]})
        return jsonify({"status": "pending"})


@app.route("/api/login-callback", methods=["POST"])
def api_login_callback():
    """Exchange OAuth code for tokens (manual paste mode)."""
    from auth import google_oauth_login, add_token
    body = request.get_json(force=True)
    code = body.get("code")
    state = body.get("state")
    device_id = body.get("device_id")

    if not code or not state or not device_id:
        return jsonify({"error": "Missing code, state, or device_id"}), 400

    result = google_oauth_login(code, state, device_id)
    if not result:
        return jsonify({"error": "Token exchange failed"}), 500

    email = result.get("user_name") or f"user_{result['user_id']}"
    add_token(
        email=email,
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        user_id=result["user_id"],
        device_id=device_id,
    )
    return jsonify({
        "success": True,
        "email": email,
        "user_id": result.get("user_id"),
        "user_name": result.get("user_name"),
        "first_login": result.get("first_login"),
    })


@app.route("/api/test-chat", methods=["POST"])
def api_test_chat():
    """Test chat from UI — returns response text."""
    body = request.get_json(force=True)
    model = body.get("model", "glm-5.2")
    message = body.get("message", "Hello!")
    stream = body.get("stream", False)

    # Forward to our own /v1/chat/completions
    import requests as r
    try:
        resp = r.post(
            f"http://127.0.0.1:{PROXY_PORT}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": message}],
                "stream": False,
            },
            timeout=120,
        )
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = data.get("usage", {})
            return jsonify({
                "success": True,
                "content": content,
                "model": data.get("model"),
                "usage": usage,
            })
        else:
            return jsonify({"error": resp.json()}), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/wallet/<email>", methods=["GET"])
def api_wallet_email(email=None):
    """Check wallet for specific account by email."""
    from auth import get_valid_token_for_email
    token, acc = get_valid_token_for_email(email)
    if not token:
        return jsonify({"error": "No valid token for " + email}), 404
    result = check_wallet(token)
    return jsonify(result)


if __name__ == "__main__":
    print(f"AutoClaw Proxy starting on {PROXY_HOST}:{PROXY_PORT}")
    print(f"Dashboard: http://localhost:{PROXY_PORT}")
    print(f"API: http://localhost:{PROXY_PORT}/v1/chat/completions")
    print(f"Models: {', '.join(MODEL_MAP.keys())}")
    list_accounts()

    # Start background wallet checker (every 5 min, non-blocking)
    wallet_thread = threading.Thread(target=_bg_wallet_checker, daemon=True)
    wallet_thread.start()
    print(f"Background wallet checker started (interval={_EXHAUSTED_CACHE_TTL}s)")

    # Start OAuth callback server on port 18432 in background thread
    # (Google registered redirect_uri = localhost:18432)
    from werkzeug.serving import make_server
    callback_server = make_server(PROXY_HOST, 18432, app, threaded=True)
    callback_thread = threading.Thread(target=callback_server.serve_forever, daemon=True)
    callback_thread.start()
    print(f"OAuth callback server on port 18432")

    # Main proxy server (blocking)
    app.run(host=PROXY_HOST, port=PROXY_PORT, threaded=True)
