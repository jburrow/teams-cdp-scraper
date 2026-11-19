#!/usr/bin/env python3
"""
teams_scrape.py - dump your own Microsoft Teams chats to JSON by borrowing the
token from a Chrome window you are already logged into, via the Chrome
DevTools Protocol (CDP).

One-time setup
--------------
Start Chrome with the DevTools port open, using a SEPARATE profile folder
(Chrome 136+ refuses to open the port on your normal profile):

    google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.teams-chrome" https://teams.microsoft.com/v2/

Log in to Teams in that window. Leave it open while the script runs.

Usage
-----
    python teams_scrape.py capture          # grab token from the open Chrome
    python teams_scrape.py dump             # export every chat
    python teams_scrape.py dump --only "Design" --max-pages 2

`dump` re-captures automatically when the token expires, as long as that
Chrome window is still running and logged in.

How the capture works
---------------------
We attach to Chrome over CDP, find the Teams tab, reload it, and watch the
requests Teams itself makes to its Chat Service ("chatsvc"). From the first
one we copy the server address and the auth header(s). Because we copy
what the real client sends, this keeps working if Microsoft moves hosts.

The API is undocumented. If something breaks, open DevTools > Network in
Teams, filter on "messages", and compare with what this script sends.
"""
import json
import re
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
TOKEN_FILE = HERE / "teams_tokens.json"
OUT_DIR = HERE / "teams_export"
CDP_URL = "http://127.0.0.1:9222"

# Matches emea.ng.msg.teams.microsoft.com/v1 and the newer proxied forms
# teams.microsoft.com/api/chatsvc/emea/v1 / teams.cloud.microsoft/api/chatsvc/emea/v1
CHATSVC_RE = re.compile(
    r"^(https://[^/]+(?:/api/chatsvc/[a-z]+)?/v1)/users/ME/conversations"
)
AUTH_HEADERS = ("authentication", "authorization", "x-skypetoken")


# --------------------------------------------------------------------------
# Step 1: attach to the running Chrome and copy the token Teams is using
# --------------------------------------------------------------------------
def capture(timeout_s: int = 60) -> dict:
    from playwright.sync_api import sync_playwright

    captured: dict = {}

    def on_request(req):
        if captured.get("base"):
            return
        m = CHATSVC_RE.match(req.url)
        if not m:
            return
        # all_headers() includes headers Chrome adds itself, not just ones
        # set by page JavaScript - this is where auth often hides.
        try:
            hdrs = req.all_headers()
        except Exception:
            hdrs = req.headers
        auth = {k: v for k, v in hdrs.items() if k.lower() in AUTH_HEADERS}
        if auth:
            captured["base"] = m.group(1)
            captured["headers"] = auth

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            sys.exit(f"[-] Couldn't reach Chrome at {CDP_URL}.\n"
                     f"    Is it running with --remote-debugging-port=9222 ?\n    ({e})")

        ctx = browser.contexts[0]
        ctx.on("request", on_request)

        teams = [pg for pg in ctx.pages
                 if "teams.microsoft.com" in pg.url or "teams.cloud.microsoft" in pg.url]
        if teams:
            page = teams[0]
            print(f"[*] Found Teams tab: {page.url[:70]}  - reloading it")
            page.reload()
        else:
            print("[*] No Teams tab open - opening one")
            page = ctx.new_page()
            page.goto("https://teams.microsoft.com/v2/")

        start = time.time()
        while not captured.get("base") and time.time() - start < timeout_s:
            page.wait_for_timeout(1000)

        # Disconnects only; your Chrome window stays open.
        browser.close()

    if not captured.get("base"):
        sys.exit("[-] Saw no Chat Service traffic. Are you logged in? "
                 "Try clicking into a chat, then run capture again.")

    captured["captured_at"] = int(time.time())
    TOKEN_FILE.write_text(json.dumps(captured, indent=2))
    TOKEN_FILE.chmod(0o600)  # only you can read it - it's a live credential
    print(f"[+] Chat service: {captured['base']}")
    print(f"[+] Auth headers: {', '.join(captured['headers'])}")
    print(f"[+] Saved to {TOKEN_FILE}")
    return captured


# --------------------------------------------------------------------------
# Step 2: call the Chat Service directly with requests
# --------------------------------------------------------------------------
class Client:
    def __init__(self):
        tok = json.loads(TOKEN_FILE.read_text()) if TOKEN_FILE.exists() else capture()
        self._load(tok)

    def _load(self, tok):
        self.base = tok["base"]
        self.s = requests.Session()
        self.s.headers.update(tok["headers"])
        self.s.headers["Accept"] = "application/json"

    def get(self, url, params=None, _retried=False):
        r = self.s.get(url, params=params, timeout=30)
        if r.status_code == 401 and not _retried:
            print("[!] Token expired - re-capturing from Chrome")
            self._load(capture())
            return self.get(url, params, _retried=True)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "10"))
            print(f"[!] Rate limited, sleeping {wait}s")
            time.sleep(wait)
            return self.get(url, params, _retried)
        r.raise_for_status()
        return r.json()

    def conversations(self, page_size=100):
        url = f"{self.base}/users/ME/conversations"
        params = {"view": "msnp24Equivalent", "pageSize": page_size}
        while url:
            data = self.get(url, params)
            yield from data.get("conversations", [])
            url = data.get("_metadata", {}).get("backwardLink")
            params = None  # backwardLink already carries the query string

    def messages(self, conv_id, page_size=200, max_pages=None):
        cid = requests.utils.quote(conv_id, safe="")
        url = f"{self.base}/users/ME/conversations/{cid}/messages"
        params = {"view": "msnp24Equivalent|supportsMessageProperties",
                  "pageSize": page_size, "startTime": 1}
        pages = 0
        while url:
            data = self.get(url, params)
            yield from data.get("messages", [])  # newest first
            url = data.get("_metadata", {}).get("backwardLink")
            params = None
            pages += 1
            if max_pages and pages >= max_pages:
                break
            time.sleep(0.3)  # be gentle


def _title(conv):
    tp = conv.get("threadProperties") or {}
    return tp.get("topic") or conv.get("id")


def dump(max_pages=None, only=None):
    c = Client()
    OUT_DIR.mkdir(exist_ok=True)
    for conv in c.conversations():
        cid = conv["id"]
        if cid.startswith("48:"):  # system streams: notifications, call logs...
            continue
        title = _title(conv)
        if only and only.lower() not in str(title).lower():
            continue
        msgs = list(c.messages(cid, max_pages=max_pages))
        msgs.reverse()  # oldest first
        safe = re.sub(r"[^\w.-]+", "_", str(title))[:80]
        (OUT_DIR / f"{safe}.json").write_text(
            json.dumps({"conversation": conv, "messages": msgs},
                       indent=2, ensure_ascii=False))
        print(f"[+] {len(msgs):5d} msgs  {title}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("capture", help="grab token from the running Chrome")
    d = sub.add_parser("dump", help="export conversations to ./teams_export")
    d.add_argument("--max-pages", type=int, help="limit pages per chat (for testing)")
    d.add_argument("--only", help="only chats whose title contains this text")
    a = ap.parse_args()
    capture() if a.cmd == "capture" else dump(a.max_pages, a.only)
