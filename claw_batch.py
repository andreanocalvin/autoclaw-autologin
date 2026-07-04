#!/usr/bin/env python3
"""
Claw - AutoClaw batch account automation.

Automates Z.ai Google OAuth → AutoClaw "Continue with Zai" → token extraction.
Uses CloakBrowser (anti-detect) + Webshare rotating proxies to bypass rate limits.

Usage:
  python claw_batch.py accounts.txt

accounts.txt format (one per line):
  email@domain.com:password

Requirements:
  - Python 3.14+
  - CloakBrowser (pip install cloakbrowser)
  - Webshare proxy pool in SQLite DB (or modify load_proxies())

Setup:
  1. pip install cloakbrowser
  2. Create accounts.txt with email:password lines
  3. Set PROXY_DB path or modify load_proxies()
  4. Set TOKENS_FILE path
  5. Run: python claw_batch.py accounts.txt
"""
import asyncio, json, sys, os, time, uuid, re, subprocess, random, traceback
import cloakbrowser

# ============================================================================
# CONFIG - Edit these paths
# ============================================================================
ZAI = "https://chat.z.ai"
AC = "https://autoclaw.z.ai/web/"
TOKENS_FILE = os.environ.get("CLAW_TOKENS_FILE", "tokens.json")
PROXY_DB = os.environ.get("CLAW_PROXY_DB", "")  # path to proxy SQLite DB
LOG_FILE = os.environ.get("CLAW_LOG_FILE", "/tmp/claw_batch.log")

# Fallback proxies if no DB (format: http://user:pass@host:port)
FALLBACK_PROXIES = []
# Example:
# FALLBACK_PROXIES = [
#     {"server": "http://1.2.3.4:8080", "username": "user", "password": "pass"},
# ]

# ============================================================================
# PROXY LOADING
# ============================================================================
def load_proxies():
    """Load proxies from SQLite DB or fallback list."""
    if PROXY_DB and os.path.exists(PROXY_DB):
        lines = subprocess.check_output(
            ["sqlite3", PROXY_DB,
             "SELECT url FROM proxy_pool WHERE status='active' LIMIT 100;"]
        ).decode().strip().split("\n")
        proxies = []
        for line in lines:
            if not line.strip():
                continue
            m = re.match(r'http://([^:]+):([^@]+)@([^:]+):(\d+)', line)
            if m:
                user, pwd, host, port = m.groups()
                proxies.append({
                    "server": f"http://{host}:{port}",
                    "username": user,
                    "password": pwd
                })
        if proxies:
            random.shuffle(proxies)
            return proxies

    if FALLBACK_PROXIES:
        return list(FALLBACK_PROXIES)

    print("ERROR: No proxies configured. Set PROXY_DB or FALLBACK_PROXIES.")
    sys.exit(1)

# ============================================================================
# TOKEN STORAGE
# ============================================================================
def save_tok(email, auth, refresh, uid, dev=None):
    """Save token to tokens.json (merge with existing)."""
    try: d = json.load(open(TOKENS_FILE))
    except: d = {"accounts": []}
    for a in d["accounts"]:
        if a.get("email") == email:
            a["access_token"] = auth
            a["refresh_token"] = refresh
            a["user_id"] = uid
            a["source"] = "zai_web_google"
            a["last_updated"] = int(time.time())
            break
    else:
        d["accounts"].append({
            "email": email,
            "access_token": auth,
            "refresh_token": refresh,
            "user_id": uid,
            "device_id": dev or str(uuid.uuid4()),
            "source": "zai_web_google",
            "created_at": int(time.time())
        })
    json.dump(d, open(TOKENS_FILE, "w"), indent=2)

# ============================================================================
# LOGGING
# ============================================================================
def log(msg):
    print(msg, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(msg + "\n")

# ============================================================================
# MAIN AUTOMATION
# ============================================================================
async def run_one(email, pwd, proxy, idx, total):
    """Run full flow for one account: Google OAuth → Z.ai → AutoClaw → token."""
    ip = proxy["server"].split("//")[-1].split(":")[0]
    log(f"\n[{idx}/{total}] {email} via {ip}")

    browser = await cloakbrowser.launch_async(headless=True, proxy=proxy)
    ctx = await browser.new_context(viewport={"width": 1280, "height": 720})
    page = await ctx.new_page()

    oauth_resps = []
    async def on_resp(resp):
        if "zai-oauth" in resp.url:
            try:
                body = await resp.text()
                oauth_resps.append({"url": resp.url[:150], "status": resp.status, "body": body[:500]})
                log(f"  ⚡ {resp.url.split('/')[-1]}: {resp.status}")
            except: pass
    page.on("response", on_resp)

    try:
        # Step 1: Google login via Z.ai
        log("[1] Google OAuth...")
        try:
            await page.goto(f"{ZAI}/oauth/google/login", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
        except Exception as e:
            log(f"  ❌ Goto failed: {str(e)[:80]}")
            await browser.close()
            return False

        if "google.com" in page.url:
            try:
                el = await page.wait_for_selector('#identifierId', timeout=15000)
                await el.fill(email)
                await page.wait_for_timeout(500)
                await page.click('#identifierNext')
                await page.wait_for_timeout(2000)

                el = await page.wait_for_selector('input[name="Passwd"]', timeout=15000)
                await el.fill(pwd)
                await page.click('#passwordNext')

                success = False
                for _ in range(30):
                    try:
                        await page.wait_for_timeout(3000)
                        u = page.url

                        if "chat.z.ai" in u and "auth" not in u and "oauth" not in u:
                            log("  [1] ✓ Z.ai session!")
                            success = True
                            break

                        # Google consent page
                        if "accountchooser" in u or "consent" in u or "/signin/oauth" in u:
                            try:
                                btns = await page.query_selector_all('button, div[role="button"], a')
                                for b in btns:
                                    try:
                                        t = await b.inner_text()
                                        if "Continue" in t and await b.is_visible():
                                            log("  [1] Consent - Continue...")
                                            await b.click()
                                            await page.wait_for_timeout(3000)
                                            break
                                    except: pass
                                # Account chooser
                                if "accountchooser" in u:
                                    els = await page.query_selector_all(
                                        '[data-email], [data-identifier], li, div[data-value], ul li')
                                    for e in els:
                                        try:
                                            t = await e.inner_text()
                                            if email in t:
                                                log(f"  [1] Chooser - click...")
                                                await e.click()
                                                await page.wait_for_timeout(3000)
                                                break
                                        except: pass
                            except: pass

                        if "SetSID" in u:
                            continue
                    except:
                        continue

                if not success:
                    log(f"  [1] ⚠️ URL: {page.url[:70]}")

            except Exception as e:
                log(f"  ❌ Google error: {str(e)[:80]}")

        # Step 2: AutoClaw
        log("[2] AutoClaw...")
        await page.goto(AC, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        btn = await page.query_selector('button:has-text("去注册")')
        if btn:
            await btn.click()
            await page.wait_for_timeout(2000)

        # Capture popup
        popup_url = None
        async def on_popup(popup):
            nonlocal popup_url
            try:
                await popup.wait_for_load_state(timeout=10000)
                popup_url = popup.url
                await popup.close()
            except:
                try: popup_url = popup.url; await popup.close()
                except: pass
        page.on("popup", on_popup)

        zai_btn = await page.query_selector('button:has-text("Continue with Zai")')
        if not zai_btn:
            log("  ❌ No Zai button")
            await browser.close()
            return False

        log("[2] Click Continue with Zai...")
        await zai_btn.click()
        for _ in range(10):
            await page.wait_for_timeout(1000)
            if popup_url:
                break

        if not popup_url:
            log("  ❌ No popup")
            await browser.close()
            return False

        # Ensure Z.ai session
        await page.goto(ZAI, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(2000)

        # Step 3: OAuth consent
        log("[3] OAuth consent...")
        await page.goto(popup_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        consent = await page.query_selector('button:has-text("Continue")')
        if consent and await consent.is_visible():
            log("[3] Click Continue...")
            cb = await page.query_selector('input[type="checkbox"]')
            if cb and not await cb.is_checked():
                await cb.check()
            await consent.click()
            await page.wait_for_timeout(8000)

        # Handle security agreement popup
        for _ in range(8):
            agree = await page.query_selector('button:has-text("我已阅读并同意")')
            if agree:
                log("[3] Agreement found!")
                d = await agree.is_disabled()
                if d:
                    log("[3] Scrolling...")
                    await page.evaluate("""() => {
                        document.querySelectorAll('*').forEach(el => {
                            if (el.scrollHeight > el.clientHeight) el.scrollTop = el.scrollHeight;
                        });
                    }""")
                    await page.wait_for_timeout(1500)
                    await agree.evaluate(
                        "el => { el.disabled = false; el.removeAttribute('disabled'); }")
                log("[3] Click agree...")
                await agree.click()
                await page.wait_for_timeout(3000)
                break
            await page.wait_for_timeout(2000)

        # Step 4: Extract token from localStorage
        log("[4] Token...")
        tok = None
        for _ in range(10):
            await page.wait_for_timeout(2000)
            tok = await page.evaluate("localStorage.getItem('autoclaw.web.authToken')")
            if tok:
                break

        if tok:
            refresh = await page.evaluate("localStorage.getItem('autoclaw.web.refreshToken')")
            uid = await page.evaluate("localStorage.getItem('autoclaw.web.userId')")
            dev = await page.evaluate("localStorage.getItem('autoclaw.web.deviceId')")
            log(f"  ✅ Token: {tok[:40]}...")
            save_tok(email, tok, refresh, uid, dev)
            await browser.close()
            return True
        else:
            log("  ❌ No token")
            for r in oauth_resps:
                log(f"    {r['status']} {r['body'][:150]}")
            await browser.close()
            return False

    except Exception as e:
        log(f"  ❌ EXCEPTION: {str(e)[:100]}")
        try: await browser.close()
        except: pass
        return False

# ============================================================================
# BATCH RUNNER
# ============================================================================
async def main():
    accounts_file = sys.argv[1] if len(sys.argv) > 1 else "accounts.txt"

    # Load accounts
    ACCOUNTS = []
    with open(accounts_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(":")
            if len(parts) >= 2:
                ACCOUNTS.append((parts[0], parts[1]))

    PROXIES = load_proxies()

    # Clear log
    open(LOG_FILE, "w").close()

    total = min(len(ACCOUNTS), len(PROXIES))
    if len(ACCOUNTS) > len(PROXIES):
        log(f"⚠️ {len(ACCOUNTS)} accounts but only {len(PROXIES)} proxies")
        log(f"   Will process first {total} accounts. Run again for remaining.")

    log(f"=== CLAW BATCH: {total} accounts, {len(PROXIES)} proxies ===")
    log(f"Start: {time.strftime('%H:%M:%S')}")

    results = []
    success = 0
    fail = 0

    for i in range(total):
        email, pwd = ACCOUNTS[i]
        proxy = PROXIES[i % len(PROXIES)]  # wrap if more accounts than proxies

        ok = await run_one(email, pwd, proxy, i+1, total)
        results.append((email, ok))

        if ok:
            success += 1
        else:
            fail += 1

        log(f"  [{i+1}/{total}] {'✅' if ok else '❌'} {email}")
        log(f"  Progress: {success} success, {fail} fail, {total-i-1} remaining")
        log("")

    log(f"\n{'='*60}")
    log(f"  FINAL: {success}/{total} success ({success*100//total}%)")
    log(f"  Failed: {fail}")
    log(f"  End: {time.strftime('%H:%M:%S')}")
    log(f"{'='*60}")

    if fail > 0:
        log("\nFailed accounts:")
        for email, ok in results:
            if not ok:
                log(f"  ❌ {email}")

if __name__ == "__main__":
    asyncio.run(main())
