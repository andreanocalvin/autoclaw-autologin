"""AutoClaw Login Helper — Interactive Google OAuth login

Usage:
  python login.py              # Interactive: generate URL, wait for callback, save token
  python login.py --list       # List all stored accounts
  python login.py --refresh    # Force refresh all tokens
  python login.py --check      # Check profile + wallet for first account
"""

import sys
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from auth import (
    google_oauth_url, google_oauth_login, add_token,
    list_accounts, refresh_all, check_profile, check_wallet,
    load_tokens,
)

# ── Local callback server (port 18432 — matches AutoClaw redirect_uri) ──
CALLBACK_PORT = 18432
_callback_data = {"code": None, "state": None, "error": None}


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "code" in params:
            _callback_data["code"] = params["code"][0]
            _callback_data["state"] = params.get("state", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Login OK!</h1><p>You can close this tab.</p></body></html>")
        else:
            _callback_data["error"] = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Login Failed</h1></body></html>")

    def log_message(self, format, *args):
        pass  # Suppress logs


def interactive_login():
    """Run interactive Google OAuth login flow."""
    print("\n=== AutoClaw Google OAuth Login ===\n")

    # Step 1: Get OAuth URL
    print("[1/3] Requesting OAuth URL...")
    oauth_url, state, device_id = google_oauth_url()
    if not oauth_url:
        print("ERROR: Failed to get OAuth URL")
        return

    print(f"\n[2/3] Open this URL in your browser and login with Google:\n")
    print(oauth_url)
    print(f"\n(State: {state})")
    print(f"(Device ID: {device_id})")

    # Step 2: Start local callback server
    print(f"\nListening for callback on port {CALLBACK_PORT}...")
    server = HTTPServer(("127.0.0.1", CALLBACK_PORT), CallbackHandler)
    server.timeout = 300  # 5 min timeout

    # Wait for callback
    print("Waiting for Google redirect...")
    server.handle_request()
    server.server_close()

    if _callback_data["error"]:
        print(f"\nERROR: OAuth error: {_callback_data['error']}")
        return

    if not _callback_data["code"]:
        print("\nERROR: No code received. Timeout or cancelled.")
        return

    code = _callback_data["code"]
    received_state = _callback_data["state"]

    print(f"\nGot code: {code[:20]}...")
    print(f"State match: {received_state == state}")

    # Step 3: Exchange code for tokens
    print("\n[3/3] Exchanging code for tokens...")
    result = google_oauth_login(code, state, device_id)
    if not result:
        print("ERROR: Token exchange failed")
        return

    print(f"\n=== Login Success! ===")
    print(f"  User ID:   {result.get('user_id')}")
    print(f"  User Name: {result.get('user_name')}")
    print(f"  First Login: {result.get('first_login')}")
    print(f"  Access Token: {result['access_token'][:50]}...")
    print(f"  Refresh Token: {result['refresh_token'][:50]}...")

    # Get email from token or ask user
    email = result.get("user_name") or input("\nEnter email for this account: ").strip()
    if not email:
        email = f"user_{result['user_id']}"

    add_token(
        email=email,
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        user_id=result["user_id"],
        device_id=device_id,
    )
    print(f"\nToken saved as: {email}")
    print("You can now use the proxy!")


def manual_login():
    """Manual login — paste callback URL."""
    print("\n=== AutoClaw Manual Login ===\n")

    print("[1/3] Requesting OAuth URL...")
    oauth_url, state, device_id = google_oauth_url()
    if not oauth_url:
        print("ERROR: Failed to get OAuth URL")
        return

    print(f"\nOpen this URL, login with Google:\n")
    print(oauth_url)

    print(f"\nAfter login, browser redirects to:")
    print(f"  http://localhost:18432/auth/callback-google?code=XXX&state={state}")
    print(f"\nThe page will error (connection refused) — that's OK!")
    print(f"Copy the FULL URL from your browser address bar.\n")

    callback_url = input("Paste callback URL here: ").strip()

    # Parse code and state from URL
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(callback_url)
    params = parse_qs(parsed.query)

    if "code" not in params:
        print("ERROR: No code found in URL")
        return

    code = params["code"][0]
    received_state = params.get("state", [""])[0]

    print(f"\nCode: {code[:20]}...")
    print(f"State match: {received_state == state}")

    print("\n[3/3] Exchanging code for tokens...")
    result = google_oauth_login(code, state, device_id)
    if not result:
        print("ERROR: Token exchange failed")
        return

    print(f"\n=== Login Success! ===")
    print(f"  User ID:   {result.get('user_id')}")
    print(f"  User Name: {result.get('user_name')}")
    print(f"  Access Token: {result['access_token'][:50]}...")
    print(f"  Refresh Token: {result['refresh_token'][:50]}...")

    email = result.get("user_name") or input("\nEnter email for this account: ").strip()
    if not email:
        email = f"user_{result['user_id']}"

    add_token(
        email=email,
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        user_id=result["user_id"],
        device_id=device_id,
    )
    print(f"\nToken saved as: {email}")


def check_account():
    """Check profile + wallet for first account."""
    data = load_tokens()
    if not data["accounts"]:
        print("No accounts. Login first.")
        return

    acc = data["accounts"][0]
    print(f"\n=== Account Check: {acc['email']} ===\n")

    print("--- Profile ---")
    profile = check_profile(acc["access_token"])
    print(profile)

    print("\n--- Wallet ---")
    wallet = check_wallet(acc["access_token"])
    print(wallet)


if __name__ == "__main__":
    if "--list" in sys.argv:
        list_accounts()
    elif "--refresh" in sys.argv:
        refresh_all()
    elif "--check" in sys.argv:
        check_account()
    elif "--manual" in sys.argv:
        manual_login()
    else:
        # Default: interactive login
        # Try interactive first, fallback to manual if port 18432 is taken
        try:
            interactive_login()
        except OSError as e:
            if "address already in use" in str(e).lower() or "10048" in str(e):
                print(f"\nPort {CALLBACK_PORT} is busy. Use manual mode:")
                manual_login()
            else:
                raise
