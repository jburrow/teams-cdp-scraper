# teams-cdp-scraper

Dump your own Microsoft Teams chats to JSON by borrowing the auth token from a
Chrome window you're already logged into, over the Chrome DevTools Protocol (CDP).

The script attaches to Chrome, reloads the Teams tab, and copies the server
address and auth headers from the first request Teams makes to its Chat Service
(`chatsvc`). It then calls that API directly with `requests`.

> The Chat Service API is undocumented and may change. Use this only on your own
> account and in line with your organisation's policies.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Playwright is only used to attach to your existing Chrome over CDP, so you don't
need `playwright install`.

## Usage

1. Start a separate Chrome profile with the DevTools port open on localhost
   (Chrome 136+ won't open the port on your normal profile):

   ```bash
   ./teams-chrome.sh
   ```

2. Log in to Teams in that window and leave it open.

3. Capture the token, then export:

   ```bash
   python teams_scrape.py capture
   python teams_scrape.py dump
   python teams_scrape.py dump --only "Design" --max-pages 2   # test on one chat
   ```

`dump` re-captures automatically if the token expires, as long as that Chrome
window is still running and logged in. Chats are written to `teams_export/`,
one JSON file per conversation, oldest message first.

## Security

- `teams_tokens.json` is a live credential. It's saved with mode `0600` and is
  git-ignored. Don't share it.
- `teams_export/` holds your chat history and is git-ignored too.
- While the debugging Chrome is running, any program on your machine can control
  it. Close it when you're done.

## Troubleshooting

If the capture or export breaks, open DevTools > Network in Teams, filter on
`messages`, and compare the request URL and headers with what the script sends.
