#!/usr/bin/env python3
"""
Claw - Single account test.

Test the full flow with one account + one proxy.
Good for debugging before running batch.

Usage:
  python claw_single.py email@domain.com password 0

Last arg is proxy index (0-based).
"""
import asyncio, json, sys, os, time, uuid, re, subprocess
import cloakbrowser

ZAI = "https://chat.z.ai"
AC = "https://autoclaw.z.ai/web/"
TOKENS_FILE = os.environ.get("CLAW_TOKENS_FILE", "tokens.json")
PROXY_DB = os.environ.get("CLAW_PROXY_DB", "")

def load_proxies():
    if PROXY_DB and os.path.exists(PROXY_DB):
        lines = subprocess.check_output(
            ["sqlite3", PROXY_DB,
             "SELECT url FROM proxy_pool WHERE status='active' LIMIT 100;"]
        ).decode().strip().split("\n")
        proxies = []
        for line in lines:
            if not line.strip(): continue
            m = re.match(r'http://([^:]+):([^@]+)@([^:]+):(\d+)', line)
            if m:
                user, pwd, host, port = m.groups()
                proxies.append({"server": f"http://{host}:{port}",
                                "username": user, "password": pwd})
        return proxies
    print("No proxies. Set CLAW_PROXY_DB env var.")
    sys.exit(1)

def save_tok(email, auth, refresh, uid, dev=None):
    try: d = json.load(open(TOKENS_FILE))
    except: d = {"accounts": []}
    for a in d["accounts"]:
        if a.get("email") == email:
            a["access_token"] = auth; a["refresh_token"] = refresh
            a["user_id"] = uid; a["source"] = "zai_web_google"
            a["last_updated"] = int(time.time()); break
    else:
        d["accounts"].append({"email": email, "access_token": auth,
            "refresh_token": refresh, "user_id": uid,
            "device_id": dev or str(uuid.uuid4()),
            "source": "zai_web_google", "created_at": int(time.time())})
    json.dump(d, open(TOKENS_FILE, "w"), indent=2)
    print(f"  💾 Saved: {email}")

async def main():
    email = sys.argv[1] if len(sys.argv) > 1 else "test@example.com"
    pwd = sys.argv[2] if len(sys.argv) > 2 else "password"
    proxy_idx = int(sys.argv[3]) if len(sys.argv) > 3 else 0

    proxies = load_proxies()
    proxy = proxies[proxy_idx % len(proxies)]
    ip = proxy["server"].split("//")[-1].split(":")[0]
    print(f"=== {email} via {ip} ===")

    browser = await cloakbrowser.launch_async(headless=True, proxy=proxy)
    ctx = await browser.new_context(viewport={"width": 1280, "height": 720})
    page = await ctx.new_page()

    oauth_resps = []
    async def on_resp(resp):
        if "zai-oauth" in resp.url:
            try:
                body = await resp.text()
                oauth_resps.append({"url": resp.url[:150], "status": resp.status, "body": body[:500]})
                print(f"  ⚡ {resp.url.split('/')[-1]}: {resp.status}")
            except: pass
    page.on("response", on_resp)

    try:
        print("[1] Google OAuth...")
        await page.goto(f"{ZAI}/oauth/google/login", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        if "google.com" in page.url:
            el = await page.wait_for_selector('#identifierId', timeout=15000)
            await el.fill(email)
            await page.wait_for_timeout(500)
            await page.click('#identifierNext')
            await page.wait_for_timeout(2000)

            el = await page.wait_for_selector('input[name="Passwd"]', timeout=15000)
            await el.fill(pwd)
            await page.click('#passwordNext')

            for _ in range(30):
                try:
                    await page.wait_for_timeout(3000)
                    u = page.url
                    print(f"  [1] {u[:70]}")

                    if "chat.z.ai" in u and "auth" not in u and "oauth" not in u:
                        print("  [1] ✓ Z.ai session!")
                        break

                    if "accountchooser" in u or "consent" in u or "/signin/oauth" in u:
                        btns = await page.query_selector_all('button, div[role="button"], a')
                        for b in btns:
                            try:
                                t = await b.inner_text()
                                if "Continue" in t and await b.is_visible():
                                    print("  [1] Consent - Continue...")
                                    await b.click()
                                    await page.wait_for_timeout(3000)
                                    break
                            except: pass
                        if "accountchooser" in u:
                            els = await page.query_selector_all('[data-email], li, div[data-value], ul li')
                            for e in els:
                                try:
                                    t = await e.inner_text()
                                    if email in t:
                                        await e.click()
                                        await page.wait_for_timeout(3000)
                                        break
                                except: pass
                except: continue

        print("[2] AutoClaw...")
        await page.goto(AC, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        btn = await page.query_selector('button:has-text("去注册")')
        if btn:
            await btn.click()
            await page.wait_for_timeout(2000)

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
            print("  ❌ No Zai button")
            await browser.close()
            return

        print("[2] Click Continue with Zai...")
        await zai_btn.click()
        for _ in range(10):
            await page.wait_for_timeout(1000)
            if popup_url: break

        if not popup_url:
            print("  ❌ No popup")
            await browser.close()
            return

        await page.goto(ZAI, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(2000)

        print("[3] OAuth consent...")
        await page.goto(popup_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        consent = await page.query_selector('button:has-text("Continue")')
        if consent and await consent.is_visible():
            print("[3] Click Continue...")
            cb = await page.query_selector('input[type="checkbox"]')
            if cb and not await cb.is_checked():
                await cb.check()
            await consent.click()
            await page.wait_for_timeout(8000)

        for _ in range(8):
            agree = await page.query_selector('button:has-text("我已阅读并同意")')
            if agree:
                print("[3] Agreement found!")
                d = await agree.is_disabled()
                if d:
                    print("[3] Scrolling...")
                    await page.evaluate("""() => {
                        document.querySelectorAll('*').forEach(el => {
                            if (el.scrollHeight > el.clientHeight) el.scrollTop = el.scrollHeight;
                        });
                    }""")
                    await page.wait_for_timeout(1500)
                    await agree.evaluate("el => { el.disabled = false; el.removeAttribute('disabled'); }")
                print("[3] Click agree...")
                await agree.click()
                await page.wait_for_timeout(3000)
                break
            await page.wait_for_timeout(2000)

        print("[4] Token...")
        tok = None
        for _ in range(10):
            await page.wait_for_timeout(2000)
            tok = await page.evaluate("localStorage.getItem('autoclaw.web.authToken')")
            if tok: break

        if tok:
            refresh = await page.evaluate("localStorage.getItem('autoclaw.web.refreshToken')")
            uid = await page.evaluate("localStorage.getItem('autoclaw.web.userId')")
            dev = await page.evaluate("localStorage.getItem('autoclaw.web.deviceId')")
            print(f"  ✅ Token: {tok[:50]}...")
            save_tok(email, tok, refresh, uid, dev)
        else:
            print("  ❌ No token")
            for r in oauth_resps:
                print(f"    {r['status']} {r['body'][:200]}")

    except Exception as e:
        print(f"  ❌ EXCEPTION: {e}")
    finally:
        await browser.close()

asyncio.run(main())
