#!/usr/bin/env python3
"""
AutoClaw Auto-Login — Google OAuth automation for AutoClaw/Z.ai
Copies Google SSO handler pattern from qoder_autologin.py.

Input:  email:password  (Google account)
Output: AutoClaw access_token + refresh_token saved to tokens.json

Prerequisites:
  - Proxy running: python proxy.py (ports 31000 + 18432)
  - cloakbrowser installed: pip install cloakbrowser

Usage:
  python autoclaw_autologin.py email@gmail.com:password123
  python autoclaw_autologin.py --batch accounts.txt
  python autoclaw_autologin.py --test email@gmail.com:password123
  python autoclaw_autologin.py --headless --batch accounts.txt
"""

import asyncio
import sys
import os
import time
import uuid
import json
import hashlib
import requests as req_lib

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ── Config from autoclaw-proxy ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    APP_ID, APP_KEY, PRODUCT, VERSION, PLATFORM,
    USER_API_BASE, GOOGLE_OAUTH_URL, GOOGLE_OAUTH_LOGIN,
    TOKENS_FILE,
)
from auth import google_oauth_url, google_oauth_login, add_token, load_tokens

HEADLESS = False
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
PROXY_BASE = "http://localhost:31000"


def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    pfx = {"INFO": "ℹ", "OK": "✅", "ERR": "❌", "DBG": "🔍", "WAIT": "⏳"}.get(level, " ")
    print(f"[{ts}] {pfx} {msg}", flush=True)

def dbg(msg):
    if DEBUG:
        log(msg, "DBG")


# ══════════════════════════════════════════════════════════════════════
#  Phase 1 — Get OAuth URL via proxy API
# ══════════════════════════════════════════════════════════════════════
def get_oauth_url():
    """Call proxy /api/login-url to get OAuth URL + state + device_id."""
    resp = req_lib.post(f"{PROXY_BASE}/api/login-url", timeout=15)
    data = resp.json()
    if "oauth_url" in data:
        return data["oauth_url"], data["state"], data["device_id"]
    log(f"Failed to get OAuth URL: {data}", "ERR")
    return None, None, None


# ══════════════════════════════════════════════════════════════════════
#  Phase 2 — Browser Automation (Google login)
# ══════════════════════════════════════════════════════════════════════
async def automate_google_login(oauth_url, email, password):
    """Open OAuth URL in CloakBrowser, handle Google login, wait for callback."""
    from cloakbrowser import launch_async

    log(f"Opening OAuth URL for {email}...")
    dbg(f"URL: {oauth_url[:100]}...")

    browser = await launch_async(
        headless=HEADLESS,
        humanize=True,
    )
    ctx = await browser.new_context(
        viewport={"width": 500, "height": 700},
        locale="en-US",
    )

    page = await ctx.new_page()
    page.set_default_timeout(30000)

    # Auto-dismiss dialogs
    async def _auto_dismiss(dialog):
        try:
            await dialog.dismiss()
        except:
            pass
    page.on("dialog", lambda d: asyncio.ensure_future(_auto_dismiss(d)))

    state = {"login_done": False, "error": None}

    try:
        await page.goto(oauth_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(1)
        url = page.url
        log(f"Page: {url[:80]}...")

        # Should be on accounts.google.com directly (OAuth URL goes straight to Google)
        if "accounts.google.com" in url or "accounts.google.co" in url:
            log("On Google login page. Automating...", "OK")
            await _handle_google_login(page, email, password)
        else:
            dbg(f"Unexpected page: {url}")

        # Quick check: did we already hit callback?
        try:
            content = await page.content()
            if "Login Success" in content or "localhost:18432" in page.url:
                log("Login Success detected! Grabbing token...", "OK")
                state["login_done"] = True
        except:
            pass

        # Wait for redirect to localhost:18432/callback (or any non-Google URL)
        for i in range(60):
            if state["login_done"]:
                break
            await asyncio.sleep(0.5)
            try:
                url = page.url
                content = await page.content()
            except:
                break

            # Check if we hit the callback OR page shows Login Success
            if "localhost:18432" in url or "callback-google" in url or "Login Success" in content:
                log("Redirected to callback! Token exchange should be happening...", "OK")
                state["login_done"] = True
                break

            # Check if we left Google (redirected somewhere else)
            if "accounts.google.com" not in url and "accounts.google.co" not in url:
                log(f"Left Google. Now at: {url[:80]}", "OK")
                state["login_done"] = True
                break

            if i % 10 == 0 and i > 0:
                dbg(f"Still waiting... ({i*0.5:.0f}s) url={url[:60]}")

    except Exception as e:
        log(f"Browser error: {e}", "ERR")
        state["error"] = str(e)
    finally:
        try:
            await browser.close()
        except:
            pass

    return state


# ══════════════════════════════════════════════════════════════════════
#  Google Login Handler (copied from qoder_autologin.py)
# ══════════════════════════════════════════════════════════════════════
async def _handle_google_login(page, email, password):
    """Handle Google's login page: email → password → consent → redirect."""
    log(f"[{email}] On Google login page. Automating...")

    for attempt in range(60):
        try:
            url = page.url
        except:
            log(f"[{email}] Page closed/navigated away", "OK")
            return

        # ── Check if we left Google (redirect back to callback) ──
        if "accounts.google.com" not in url and "accounts.google.co" not in url:
            log(f"[{email}] Left Google. Now at: {url[:60]}", "OK")
            return

        # ── Check if page shows Login Success ──
        try:
            content = await page.content()
            if "Login Success" in content:
                log(f"[{email}] Login Success detected in page!", "OK")
                return
        except:
            pass

        # ── Email step ──
        try:
            email_visible = await page.evaluate("""() => {
                const el = document.querySelector('#identifierId');
                return el && el.offsetParent !== null;
            }""")
        except:
            email_visible = False
            await asyncio.sleep(0.5)
            continue

        if email_visible:
            dbg(f"[{email}] Filling Google email...")
            loc = page.locator("#identifierId").first
            await loc.click(force=True)
            await asyncio.sleep(0.2)
            await loc.press("Control+a")
            await loc.press("Backspace")
            await loc.press_sequentially(email, delay=40)
            await asyncio.sleep(0.3)
            await page.evaluate("""() => {
                const btn = document.querySelector('#identifierNext button');
                if (btn) btn.click();
            }""")
            # Wait for password field
            for _w in range(10):
                await asyncio.sleep(0.5)
                try:
                    pwd_check = await page.evaluate("""() => {
                        for (const el of document.querySelectorAll(
                                'input[name="Passwd"], input[type="password"]')) {
                            if (el.offsetParent !== null) return true;
                        }
                        return false;
                    }""")
                    if pwd_check:
                        break
                except:
                    pass
            await asyncio.sleep(0.5)
            continue

        # ── Password step ──
        try:
            pwd_visible = await page.evaluate("""() => {
                for (const el of document.querySelectorAll(
                        'input[name="Passwd"], input[type="password"]')) {
                    if (el.offsetParent !== null) return true;
                }
                return false;
            }""")
        except:
            pwd_visible = False
            await asyncio.sleep(0.5)
            continue

        if pwd_visible:
            dbg(f"[{email}] Filling Google password...")
            try:
                loc = page.locator('input[name="Passwd"]').first
                if await loc.count() == 0 or not await loc.is_visible():
                    loc = page.locator('input[type="password"]').first
                await loc.click(force=True)
                await asyncio.sleep(0.2)
                await loc.press("Control+a")
                await loc.press("Backspace")
                await loc.press_sequentially(password, delay=30)
                await asyncio.sleep(0.2)
                await page.evaluate("""() => {
                    const btn = document.querySelector('#passwordNext button');
                    if (btn) btn.click();
                }""")
            except Exception as e:
                dbg(f"[{email}] Password field error (likely navigating): {e}")
                await asyncio.sleep(1)
                continue
            # After password submit, Google navigates (SetSID → OAuth consent)
            # Wait for navigation to settle — DON'T try to detect password again
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except:
                pass
            await asyncio.sleep(1)

            # Check if we've left Google or hit consent page
            try:
                url = page.url
                if "accounts.google.com" not in url and "accounts.google.co" not in url:
                    log(f"[{email}] Left Google after password. Now at: {url[:60]}", "OK")
                    return
            except:
                return

            # Look for consent buttons on the next page
            try:
                consent_clicked = await page.evaluate("""() => {
                    const knownIds = ['submit_approve_access', 'approve_button', 'confirm'];
                    for (const id of knownIds) {
                        const el = document.getElementById(id);
                        if (el && el.offsetParent !== null) {
                            el.click(); return 'clicked id: ' + id;
                        }
                    }
                    const buttons = document.querySelectorAll(
                        'button, [role="button"], span[role="button"]'
                    );
                    const consentTexts = [
                        'allow', 'continue', 'approve', 'confirm', 'accept', 'next',
                        'i understand', 'agree', 'done',
                        'izinkan', 'lanjutkan', 'setuju', 'terima'
                    ];
                    for (const btn of buttons) {
                        const txt = (btn.textContent || '').toLowerCase().trim();
                        if (consentTexts.some(t => txt.includes(t))) {
                            btn.click();
                            if (btn.tagName === 'SPAN' && btn.parentElement) {
                                btn.parentElement.click();
                            }
                            return 'clicked: ' + txt;
                        }
                    }
                    return null;
                }""")
                if consent_clicked:
                    dbg(f"[{email}] Post-password consent: {consent_clicked}")
                    await asyncio.sleep(1)
            except:
                pass
            continue

        # ── Consent / Agreement / Speedbump screens ──
        try:
            consent_clicked = await page.evaluate("""() => {
                // Priority 1: Known IDs
                const knownIds = ['confirm', 'submit_approve_access', 'approve_button',
                                 'next', 'identifierNext', 'passwordNext'];
                for (const id of knownIds) {
                    const el = document.getElementById(id);
                    if (el && el.offsetParent !== null) {
                        el.click(); return 'clicked id: ' + id;
                    }
                }
                // Priority 2: Known names
                const knownNames = ['confirm', 'continue', 'approve', 'accept'];
                for (const name of knownNames) {
                    const el = document.querySelector(`[name="${name}"]`);
                    if (el && el.offsetParent !== null) {
                        el.click(); return 'clicked name: ' + name;
                    }
                }
                // Priority 3: Text matching (multi-language)
                const buttons = document.querySelectorAll(
                    'button, [role="button"], span[role="button"], input[type="submit"], ' +
                    'span.VfPpkd-vQzf8d, div.VfPpkd-RLmnJb, [jsname="V67aGc"]'
                );
                const consentTexts = [
                    'i understand', 'i agree', 'agree', 'allow', 'continue', 'next',
                    'approve', 'confirm', 'accept', 'got it', 'accept all', 'done',
                    'i accept', 'accept & continue',
                    'saya mengerti', 'saya setuju', 'setuju', 'lanjutkan', 'terima',
                    'izinkan', 'konfirmasi', 'mengerti', 'oke', 'ya'
                ];
                for (const btn of buttons) {
                    const txt = (btn.textContent || btn.value || '').toLowerCase().trim();
                    if (consentTexts.some(t => txt.includes(t) || txt === t)) {
                        btn.click();
                        if (btn.tagName === 'SPAN' && btn.parentElement && btn.parentElement.tagName === 'BUTTON') {
                            btn.parentElement.click();
                        }
                        return 'clicked text: ' + txt;
                    }
                }
                return null;
            }""")
        except Exception:
            consent_clicked = None

        if consent_clicked:
            dbg(f"[{email}] Consent: {consent_clicked}")
            await asyncio.sleep(0.5)
            continue

        # ── Choose account page ──
        try:
            account_clicked = await page.evaluate("""() => {
                const accounts = document.querySelectorAll('[data-identifier], [data-email]');
                if (accounts.length > 0) { accounts[0].click(); return 'picked first account'; }
                return null;
            }""")
            if account_clicked:
                dbg(f"[{email}] Google account: {account_clicked}")
                await asyncio.sleep(1)
                continue
        except:
            pass

        await asyncio.sleep(0.5)

    log(f"[{email}] Google login timed out (90s)", "ERR")


# ══════════════════════════════════════════════════════════════════════
#  Phase 3 — Check login result via proxy API
# ══════════════════════════════════════════════════════════════════════
def check_login_result(state=None):
    """Check if proxy captured the callback and exchanged tokens.
    Pass state for concurrent login tracking."""
    for i in range(30):
        url = f"{PROXY_BASE}/api/login-status"
        if state:
            url += f"?state={state}"
        resp = req_lib.get(url, timeout=5)
        data = resp.json()
        status = data.get("status")
        if status == "ok":
            return data.get("account")
        elif status == "error":
            log(f"Login error: {data.get('error')}", "ERR")
            return None
        time.sleep(1)
    return None


# ══════════════════════════════════════════════════════════════════════
#  Main — process single account
# ══════════════════════════════════════════════════════════════════════
async def process_account(email, password, test_only=False):
    tag = "[TEST] " if test_only else ""
    log(f"{tag}Processing: {email}")

    # Check if already in tokens.json
    tokens = load_tokens()
    existing = [a for a in tokens.get("accounts", []) if a.get("email") == email]
    if existing and not test_only:
        log(f"Already have token for {email}, skipping. Use --force to re-login.", "OK")
        return {"success": True, "email": email, "skipped": True}

    # 1. Get OAuth URL
    oauth_url, state, device_id = get_oauth_url()
    if not oauth_url:
        return {"success": False, "email": email, "error": "oauth_url_failed"}

    dbg(f"state={state}, device_id={device_id}")

    # 2. Browser automation — Google login
    result = await automate_google_login(oauth_url, email, password)

    if result.get("error") and not result.get("login_done"):
        log(f"Login failed: {result['error']}", "ERR")
        return {"success": False, "email": email, "error": result["error"]}

    # 3. Check if proxy captured the callback (use state for concurrent tracking)
    log("Checking if token was captured...", "WAIT")
    account = check_login_result(state)

    if account:
        log(f"Login success! Email: {account.get('email')}, User ID: {account.get('user_id')}", "OK")
        return {
            "success": True,
            "email": account.get("email", email),
            "user_id": account.get("user_id"),
            "first_login": account.get("first_login"),
        }

    # Fallback: check tokens.json directly
    await asyncio.sleep(2)
    tokens = load_tokens()
    new_acc = [a for a in tokens.get("accounts", []) if a.get("email") == email]
    if new_acc:
        log(f"Token found in tokens.json for {email}", "OK")
        return {"success": True, "email": email, "user_id": new_acc[0].get("user_id")}

    log("Token not captured! Check proxy logs.", "ERR")
    return {"success": False, "email": email, "error": "token_not_captured"}


# ══════════════════════════════════════════════════════════════════════
#  Concurrent batch runner
# ══════════════════════════════════════════════════════════════════════
async def run_batch(accounts, test_only=False, concurrent=1):
    """Run multiple accounts concurrently with semaphore."""
    sem = asyncio.Semaphore(concurrent)

    async def _run(email, password):
        async with sem:
            return await process_account(email, password, test_only=test_only)

    tasks = []
    for acc in accounts:
        email, pwd = acc.split(":", 1)
        tasks.append(_run(email, pwd))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    processed = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            email = accounts[i].split(":", 1)[0]
            processed.append({"success": False, "email": email, "error": str(r)})
        else:
            processed.append(r)

    return processed


# ══════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════
def parse_args():
    import argparse
    parser = argparse.ArgumentParser(
        description="AutoClaw Auto-Login — Google OAuth automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python autoclaw_autologin.py user@gmail.com:password
  python autoclaw_autologin.py --batch accounts.txt
  python autoclaw_autologin.py --batch accounts.txt --headless --concurrent 3
  python autoclaw_autologin.py --batch accounts.txt --interactive
  python autoclaw_autologin.py --test user@gmail.com:password

Account format (in file):
  email:password
  # lines starting with # are comments
  # blank lines are ignored
        """,
    )
    parser.add_argument("accounts", nargs="*",
                        help="email:password pairs (space-separated)")
    parser.add_argument("--batch", "-b", metavar="FILE",
                        help="Read accounts from file (one email:password per line)")
    parser.add_argument("--test", "-t", action="store_true",
                        help="Test mode: get token but don't save")
    parser.add_argument("--headless", action="store_true",
                        help="Run browser in headless mode (invisible)")
    parser.add_argument("--concurrent", "-c", type=int, default=1,
                        help="Number of concurrent browser sessions (default: 1)")
    parser.add_argument("--debug", "-d", action="store_true",
                        help="Enable debug output")
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Interactive mode: show info and ask before running")
    parser.add_argument("--force", action="store_true",
                        help="Re-login even if account already exists in tokens.json")
    return parser.parse_args()


async def main():
    global HEADLESS, DEBUG
    args = parse_args()

    if args.debug:
        DEBUG = True
        os.environ["DEBUG"] = "true"

    HEADLESS = args.headless

    # Check proxy is running
    try:
        r = req_lib.get(f"{PROXY_BASE}/health", timeout=5)
        if r.status_code != 200:
            log("Proxy not healthy! Start it first: python proxy.py", "ERR")
            sys.exit(1)
    except:
        log("Proxy not running! Start it first: python proxy.py", "ERR")
        sys.exit(1)

    log(f"Proxy OK ({r.json()})")

    # Load accounts
    if args.batch:
        accounts = []
        with open(args.batch) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and ":" in line:
                    accounts.append(line)
        log(f"Loaded {len(accounts)} accounts from {args.batch}")
    else:
        accounts = args.accounts

    if not accounts:
        print(__doc__)
        sys.exit(1)

    # Filter bad format
    accounts = [acc for acc in accounts if ":" in acc]

    # Skip existing (unless --force)
    if not args.test and not args.force:
        tokens = load_tokens()
        existing = {a.get("email", "").lower() for a in tokens.get("accounts", [])}
        if existing:
            before = len(accounts)
            accounts = [acc for acc in accounts if acc.split(":", 1)[0].lower() not in existing]
            skipped = before - len(accounts)
            if skipped:
                log(f"Skipped {skipped} account(s) already in tokens.json")
            if not accounts:
                log("All accounts already exist. Nothing to do.", "OK")
                sys.exit(0)

    # ── Interactive mode ──
    if args.interactive:
        print(f"  [i] Found {len(accounts)} account(s)")
        print()
        print("  ---------------------------------------------------")
        for i, acc in enumerate(accounts[:10]):
            email = acc.split(":", 1)[0]
            print(f"    {email}")
        if len(accounts) > 10:
            print(f"    ... dan {len(accounts)-10} akun lainnya")
        print("  ---------------------------------------------------")
        print()

        # Ask headless
        headless_input = input("  Headless mode? (browser invisible) [y/N]: ").strip().lower()
        HEADLESS = headless_input == "y"
        args.headless = HEADLESS
        print()

        # Ask concurrent
        conc_input = input("  Concurrent browsers (1-5) [1]: ").strip()
        try:
            conc = int(conc_input)
            conc = max(1, min(5, conc))
        except:
            conc = 1
        args.concurrent = conc
        print()

        # Summary
        mode_str = "Headless (invisible)" if HEADLESS else "Visible"
        print("  +--------------------------------------+")
        print(f"  |  Accounts:   {len(accounts)}")
        print(f"  |  Browser:    {mode_str}")
        print(f"  |  Concurrent: {args.concurrent}")
        print(f"  |  Save to:    tokens.json")
        print("  +--------------------------------------+")
        print()

        # Confirm
        confirm = input("  Start login? [Y/n]: ").strip().lower()
        if confirm == "n":
            print()
            print("  Cancelled.")
            sys.exit(0)
        print()
        print("  Starting...")
        print()

    # Header
    mode = "HEADLESS" if HEADLESS else "VISIBLE"
    test = " | TEST MODE" if args.test else ""
    log(f"AutoClaw Auto-Login | {len(accounts)} account(s) | {mode} | concurrent={args.concurrent}{test}")

    # Run
    start = time.time()
    results = await run_batch(accounts, test_only=args.test, concurrent=args.concurrent)

    # ── Retry loop for failed accounts ──
    max_retries = 3
    retry_count = 0

    while True:
        # Summary
        ok_count = sum(1 for r in results if r.get("success"))
        fail_count = len(results) - ok_count
        skipped = sum(1 for r in results if r.get("skipped"))
        elapsed = time.time() - start

        log(f"\n{'='*50}")
        log(f"SUMMARY{' (retry ' + str(retry_count) + ')' if retry_count else ''}")
        log(f"{'='*50}")
        log(f"Total: {len(results)} | OK {ok_count} (skipped: {skipped}) | FAIL {fail_count} | {elapsed:.1f}s")
        for r in results:
            s = "OK" if r.get("success") else "FAIL"
            if r.get("skipped"):
                s = "SKIP"
            e = r.get("error", "")
            log(f"  [{s}] {r['email']}{' — '+e if e else ''}")

        if fail_count == 0 or retry_count >= max_retries:
            break

        # Retry failed
        failed_emails = {r["email"] for r in results if not r.get("success")}
        failed_accounts = [acc for acc in accounts if acc.split(":", 1)[0] in failed_emails]
        if not failed_accounts:
            break

        retry_count += 1
        log(f"\nRetrying {len(failed_accounts)} failed accounts (attempt {retry_count}/{max_retries})...")
        await asyncio.sleep(3)
        retry_results = await run_batch(failed_accounts, test_only=args.test, concurrent=args.concurrent)

        # Merge results
        for i, r in enumerate(retry_results):
            email = failed_accounts[i].split(":", 1)[0]
            for j, orig in enumerate(results):
                if orig["email"] == email:
                    results[j] = r
                    break

    # Final
    ok_count = sum(1 for r in results if r.get("success"))
    log(f"\nFinal: {ok_count}/{len(results)} accounts active")


if __name__ == "__main__":
    asyncio.run(main())
