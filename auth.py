"""AutoClaw Auth — Token management, refresh, validation"""

import time
import json
import hashlib
import uuid
import threading
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from config import (
    APP_ID, APP_KEY, PRODUCT, VERSION, PLATFORM,
    USER_API_BASE, GOOGLE_OAUTH_URL, GOOGLE_OAUTH_LOGIN,
    REFRESH_URL, PROFILE_URL, WALLET_URL, LEDGER_URL,
    TOKENS_FILE, ACCESS_TOKEN_TTL, REFRESH_MARGIN,
)

_lock = threading.Lock()


def _sign_headers():
    """Generate forgeable app-signing headers."""
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


def load_tokens():
    """Load tokens from JSON file."""
    try:
        with open(TOKENS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"accounts": []}


def save_tokens(data):
    """Save tokens to JSON file (thread-safe, atomic, with wipe guard).
    Refuses to write if new account count < 50% of existing — prevents
    accidental wipe from race conditions or corrupted reads."""
    with _lock:
        # Guard: refuse to write if we'd be wiping >50% of accounts
        try:
            with open(TOKENS_FILE, "r") as f:
                existing = json.load(f)
            existing_count = len(existing.get("accounts", []))
            new_count = len(data.get("accounts", []))
            if existing_count > 0 and new_count < existing_count * 0.5:
                print(f"[auth] BLOCKED save_tokens: {new_count} accounts would replace {existing_count} (wipe guard)")
                return False
        except (FileNotFoundError, json.JSONDecodeError):
            pass  # No existing file or corrupt — allow write
        # Atomic write: write to temp file then rename
        import os
        tmp = TOKENS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, TOKENS_FILE)
        return True


def add_token(email, access_token, refresh_token, user_id, device_id, source_id="autoclaw"):
    """Add or update a token entry."""
    data = load_tokens()
    # Remove existing entry for same email
    data["accounts"] = [a for a in data["accounts"] if a.get("email") != email]
    data["accounts"].append({
        "email": email,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user_id": user_id,
        "device_id": device_id,
        "source_id": source_id,
        "added_at": int(time.time()),
        "last_refreshed": int(time.time()),
    })
    save_tokens(data)
    print(f"[auth] Token saved: {email}")


def get_valid_token():
    """Get a valid (non-expired) access token. Refresh if needed.
    Returns: (access_token, account_dict) or (None, None)
    """
    data = load_tokens()
    if not data["accounts"]:
        return None, None

    for acc in data["accounts"]:
        # Check if token needs refresh (older than TTL - margin)
        last_refreshed = acc.get("last_refreshed", 0)
        age = time.time() - last_refreshed
        if age < ACCESS_TOKEN_TTL - REFRESH_MARGIN:
            return acc["access_token"], acc

        # Try refresh
        new_token = refresh_token(acc)
        if new_token:
            return new_token, acc

    return None, None


def get_valid_token_for_email(email):
    """Get valid token for specific email."""
    data = load_tokens()
    for acc in data["accounts"]:
        if acc.get("email") == email:
            last_refreshed = acc.get("last_refreshed", 0)
            age = time.time() - last_refreshed
            if age < ACCESS_TOKEN_TTL - REFRESH_MARGIN:
                return acc["access_token"], acc
            new_token = refresh_token(acc)
            if new_token:
                return new_token, acc
    return None, None


def refresh_token(account):
    """Refresh an access token using refresh_token.
    Returns: new access_token or None
    """
    try:
        headers = _sign_headers()
        body = {
            "source_id": account.get("source_id", "autoclaw"),
            "device_id": account["device_id"],
            "refresh_token": account["refresh_token"],
        }
        resp = requests.post(REFRESH_URL, json=body, headers=headers, timeout=15, verify=False)
        data = resp.json()

        if data.get("code") == 0 and "data" in data:
            new_access = data["data"].get("access_token")
            new_refresh = data["data"].get("refresh_token", account["refresh_token"])

            if new_access:
                # Update stored token
                all_data = load_tokens()
                for a in all_data["accounts"]:
                    if a.get("email") == account["email"]:
                        a["access_token"] = new_access
                        if new_refresh:
                            a["refresh_token"] = new_refresh
                        a["last_refreshed"] = int(time.time())
                        break
                save_tokens(all_data)
                print(f"[auth] Refreshed token: {account['email']}")
                return new_access
        print(f"[auth] Refresh failed for {account['email']}: {data}")
        return None
    except Exception as e:
        print(f"[auth] Refresh error for {account['email']}: {e}")
        return None


def refresh_all():
    """Refresh all tokens. Returns count of success/fail.
    Collects all updates in-memory, saves ONCE at end (not per-account).
    This prevents race conditions that can wipe tokens.json."""
    data = load_tokens()
    success = 0
    fail = 0
    now = int(time.time())
    for acc in data["accounts"]:
        # Build refresh request from current account data
        try:
            headers = _sign_headers()
            body = {
                "source_id": acc.get("source_id", "autoclaw"),
                "device_id": acc["device_id"],
                "refresh_token": acc["refresh_token"],
            }
            resp = requests.post(REFRESH_URL, json=body, headers=headers, timeout=15, verify=False)
            resp_data = resp.json()

            if resp_data.get("code") == 0 and "data" in resp_data:
                new_access = resp_data["data"].get("access_token")
                new_refresh = resp_data["data"].get("refresh_token", acc["refresh_token"])
                if new_access:
                    acc["access_token"] = new_access
                    if new_refresh:
                        acc["refresh_token"] = new_refresh
                    acc["last_refreshed"] = now
                    success += 1
                    print(f"[auth] Refreshed: {acc['email']}")
                else:
                    fail += 1
                    print(f"[auth] Refresh failed (no access_token): {acc['email']}")
            else:
                fail += 1
                print(f"[auth] Refresh failed: {acc['email']}: {resp_data}")
        except Exception as e:
            fail += 1
            print(f"[auth] Refresh error for {acc['email']}: {e}")

    # Save ONCE at the end (not per-account) — atomic, with wipe guard
    save_tokens(data)
    print(f"[auth] Refresh all: {success} ok, {fail} fail")
    return success, fail


def check_profile(access_token):
    """Verify token validity via user-profile endpoint."""
    headers = _sign_headers()
    raw = access_token.replace("Bearer ", "")
    headers["X-Authorization"] = f"Bearer {raw}"
    try:
        resp = requests.post(PROFILE_URL, json={}, headers=headers, timeout=15, verify=False)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def check_wallet(access_token):
    """Check wallet balance (reward points)."""
    headers = _sign_headers()
    raw = access_token.replace("Bearer ", "")
    headers["authorization"] = f"Bearer {raw}"  # lowercase for assetmgr!
    try:
        resp = requests.get(WALLET_URL, headers=headers, timeout=15, verify=False)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def check_ledger(access_token):
    """Check billing ledger."""
    headers = _sign_headers()
    raw = access_token.replace("Bearer ", "")
    headers["authorization"] = f"Bearer {raw}"
    try:
        resp = requests.get(LEDGER_URL, headers=headers, timeout=15, verify=False)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def google_oauth_url(device_id=None, navigate_uri="http://localhost:18432/auth/callback-google"):
    """Step 1: Get Google OAuth URL."""
    headers = _sign_headers()
    if not device_id:
        device_id = str(uuid.uuid4())
    body = {
        "source_id": "autoclaw",
        "device_id": device_id,
        "navigate_uri": navigate_uri,
    }
    resp = requests.post(GOOGLE_OAUTH_URL, json=body, headers=headers, timeout=15, verify=False)
    data = resp.json()
    if data.get("code") == 0:
        return data["data"]["oauth_url"], data["data"]["state"], device_id
    return None, None, device_id


def google_oauth_login(code, state, device_id, navigate_uri="http://localhost:18432/auth/callback-google"):
    """Step 2: Exchange Google OAuth code for AutoClaw tokens."""
    headers = _sign_headers()
    body = {
        "code": code,
        "state": state,
        "navigate_uri": navigate_uri,
        "device_id": device_id,
        "source_id": "autoclaw",
    }
    resp = requests.post(GOOGLE_OAUTH_LOGIN, json=body, headers=headers, timeout=15, verify=False)
    data = resp.json()
    if data.get("code") == 0 and "data" in data:
        d = data["data"]
        return {
            "access_token": d.get("access_token"),
            "refresh_token": d.get("refresh_token"),
            "user_id": d.get("user_id"),
            "user_name": d.get("user_name"),
            "first_login": d.get("first_login"),
            "device_id": device_id,
        }
    print(f"[auth] OAuth login failed: {data}")
    return None


def list_accounts():
    """Print all stored accounts."""
    data = load_tokens()
    print(f"\n=== AutoClaw Accounts ({len(data['accounts'])}) ===")
    for i, acc in enumerate(data["accounts"]):
        age = time.time() - acc.get("last_refreshed", 0)
        hours_left = max(0, (ACCESS_TOKEN_TTL - age) / 3600)
        print(f"  {i+1}. {acc['email']} | user={acc.get('user_id','?')} | "
              f"token_age={age/3600:.1f}h | expires_in={hours_left:.1f}h")
    if not data["accounts"]:
        print("  (empty — add token first)")
