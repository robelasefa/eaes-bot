# EAES Result Tracker Bot

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![tests](https://github.com/robelasefa/eaes-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/robelasefa/eaes-bot/actions/workflows/tests.yml)

A Telegram bot that polls `result.eaes.et` for tracked students and notifies
each chat the moment their EUEE result is published. Drives a real Chrome
instance via [`nodriver`](https://github.com/ultrafunkamsterdam/nodriver)
since the site has no public API and sits behind Cloudflare Turnstile.

## How it works

- `/track <admission_number> <first_name>` registers a student.
- `/status` lists what you're tracking, each entry with an inline "Stop"
  button — tap it instead of typing `/stop <number>`.
- A background job checks every active entry on a timer, through one shared
  Chrome instance (`EAES_MAX_CONCURRENT` checks at a time).
- On a confirmed result, the bot messages the result and stops tracking it.
- Failed/pending checks increment an attempt counter; after
  `EAES_MAX_ATTEMPTS` the entry is dropped and the user is notified.
- Each poll cycle starts with a browser health check. If Chrome has
  crashed, the bot relaunches it and skips that cycle — attempt counters
  are never penalized for an infra failure.

## Project layout

```
bot.py       Telegram commands, scheduling, app lifecycle
scraper.py   nodriver browser automation + result-page parsing
db.py        Async SQLAlchemy models and CRUD (SQLite/Postgres/MySQL)
config.py    All environment-variable settings, read once
utils.py     Regex validation, masking, MarkdownV2 escaping
tests/       pytest suite
```

## Prerequisites

- Python 3.11+
- Chrome/Chromium installed (or set `EAES_CHROME_EXECUTABLE_PATH`)
- **Headless Linux only:** Chrome needs a display. Either run under
  `xvfb-run`, or set `EAES_USE_VIRTUAL_DISPLAY=1` and `pip install
  pyvirtualdisplay` (+ the `xvfb` system package) to let the bot manage its
  own. Windows/macOS need neither.

## Quick start

```bash
git clone <this-repo> && cd eaes-bot
python3 -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # edit .env, set TELEGRAM_BOT_TOKEN
```

Export the vars in `.env` into your shell (or use `direnv` / `python-dotenv`),
then run:

```bash
python bot.py                              # Windows / macOS
xvfb-run -a python bot.py                  # headless Linux, option A
EAES_USE_VIRTUAL_DISPLAY=1 python bot.py   # headless Linux, option B
```

This creates `tracking.db` (or connects to whatever `DATABASE_URL` points
at), launches a persistent Chrome profile under `./chrome_profile/`, and
starts polling.

## Environment variables

Full list with defaults in [`.env.example`](./.env.example). The only
required one is `TELEGRAM_BOT_TOKEN`. Most-used:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///tracking.db` | Any SQLAlchemy async URL |
| `EAES_POLL_INTERVAL_SECONDS` | `300` | Seconds between poll cycles |
| `EAES_MAX_CONCURRENT` | `3` | Concurrent browser checks per cycle |
| `EAES_MAX_TRACKED_PER_CHAT` | `10` | Cap on active trackings per chat |
| `EAES_MAX_ATTEMPTS` | `50` | Attempts before auto-drop |
| `EAES_USE_VIRTUAL_DISPLAY` | `0` | `1` to self-manage a display on headless Linux |

## Switching databases

Change `DATABASE_URL` and install the matching async driver — no code
changes needed:

```bash
pip install asyncpg     # postgresql+asyncpg://user:pass@host:5432/eaes
pip install aiomysql    # mysql+aiomysql://user:pass@host:3306/eaes
```

## Chrome profile directory

`chrome_profile/` holds the persistent session Cloudflare's trust signal
depends on:
- must be writable and persist across restarts (deleting it resets trust)
- must **not** be shared between two bot instances running at once
- back it up alongside your database if that trust state matters to you

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Covers validation regexes, DOM-to-message parsing, and async DB CRUD
(against a throwaway SQLite file — no external services needed).

## Deployment

Any process manager that sets env vars and restarts on failure works. On
Linux, a systemd unit:

```ini
[Unit]
Description=EAES Result Tracker Bot
After=network-online.target

[Service]
Type=simple
User=eaesbot
WorkingDirectory=/opt/eaes-bot
EnvironmentFile=/opt/eaes-bot/.env
ExecStart=/opt/eaes-bot/venv/bin/python bot.py
Restart=on-failure
MemoryMax=1200M

[Install]
WantedBy=multi-user.target
```

Set `EAES_USE_VIRTUAL_DISPLAY=1` in `.env` since a systemd service has no
display of its own. `MemoryMax` + `Restart=on-failure` recycle a leaked or
stuck process rather than taking the host down.

## Operational notes

- This scrapes around a third-party portal's anti-bot protection — if EAES
  changes its DOM or tightens Cloudflare, checks will start failing with
  `status=blocked`/`status=error`. Watch for those rising relative to
  `status=pending`.
- Admission numbers are always masked in logs (e.g. `12****56`).
- No automated DB backups. For SQLite in WAL mode, checkpoint first or copy
  the `.db`, `.db-wal`, and `.db-shm` files together.

## License

MIT — see [`LICENSE`](./LICENSE).
