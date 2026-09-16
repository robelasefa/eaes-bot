# EAES Result Tracker Bot

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Tests](https://github.com/robelasefa/eaes-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/robelasefa/eaes-bot/actions/workflows/tests.yml)

> **Educational project, unaffiliated with MoE.**
>
> This bot interacts with `result.eaes.et`, which does not provide a public API. It stores the admission numbers and names you choose to track in its database. Use it responsibly and comply with any applicable rules or laws. The project is provided as-is; see [LICENSE](./LICENSE).

A Telegram bot that watches the Ethiopian University Entrance Exam (EUEE) results portal and notifies students the moment their result is published.

It drives a real Chrome/Chromium browser via [`nodriver`](https://github.com/ultrafunkamsterdam/nodriver), since the results site has no public API and sits behind Cloudflare Turnstile.

## How it works

* `/track <admission_number> <first_name>` starts tracking a student.
* `/status` shows your active trackings, with a **Stop** button for each one.
* A background job periodically checks all active trackings using a shared browser.
* When a result is confirmed, the bot sends the result to the relevant chat and stops tracking that student.
* Temporary or failed checks count toward the configured attempt limit. Once the limit is reached, tracking is automatically removed and the user is notified.
* Before every poll cycle, the bot checks that the browser is healthy. If Chrome has crashed, it relaunches it and skips that cycle so students aren't penalized for a browser failure.

## Project layout

```text
bot.py       Telegram commands, scheduler, and application lifecycle
scraper.py   Browser automation and EAES result parsing
db.py        Async SQLAlchemy models and database operations
config.py    Environment-variable configuration
utils.py     Validation, masking, and MarkdownV2 helpers
tests/       pytest test suite
```

## Prerequisites

* Python 3.11+
* Chrome or Chromium
* A Telegram bot token

For headless Linux, Chrome also needs a display. You can either:

* run the bot with `xvfb-run`, or
* set `EAES_USE_VIRTUAL_DISPLAY=1` and install `pyvirtualdisplay` and the system `xvfb` package.

Windows and macOS do not need this extra setup.

If Chrome is not installed in a standard location, set `EAES_CHROME_EXECUTABLE_PATH` to its executable path.

## Quick start

```bash
git clone https://github.com/robelasefa/eaes-bot.git
cd eaes-bot

python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
```

Edit `.env` and add your `TELEGRAM_BOT_TOKEN`, then start the bot:

```bash
python bot.py
```

On a headless Linux server:

```bash
xvfb-run -a python bot.py
```

Or let the bot manage its own virtual display:

```bash
EAES_USE_VIRTUAL_DISPLAY=1 python bot.py
```

The bot creates `tracking.db` by default and maintains a persistent Chrome profile in `./chrome_profile/`.

## Configuration

See [`.env.example`](./.env.example) for the complete list of options.

| Variable                     | Default                           | Purpose                                    |
| ---------------------------- | --------------------------------- | ------------------------------------------ |
| `TELEGRAM_BOT_TOKEN`         | —                                 | **Required.** Telegram bot token           |
| `DATABASE_URL`               | `sqlite+aiosqlite:///tracking.db` | Async SQLAlchemy database URL              |
| `EAES_POLL_INTERVAL_SECONDS` | `300`                             | Time between poll cycles                   |
| `EAES_MAX_CONCURRENT`        | `3`                               | Browser checks running at once             |
| `EAES_MAX_TRACKED_PER_CHAT`  | `10`                              | Maximum active trackings per chat          |
| `EAES_MAX_ATTEMPTS`          | `50`                              | Failed/pending checks before auto-removal  |
| `EAES_USE_VIRTUAL_DISPLAY`   | `0`                               | Manage a virtual display on headless Linux |

## Database

The bot uses SQLAlchemy's async interface, with SQLite as the default.

To use PostgreSQL or MySQL instead, change `DATABASE_URL` and install the corresponding async driver:

```bash
pip install asyncpg
# postgresql+asyncpg://user:pass@host:5432/eaes
```

```bash
pip install aiomysql
# mysql+aiomysql://user:pass@host:3306/eaes
```

No application code changes are required.

## Chrome profile

The `chrome_profile/` directory stores the browser's persistent session state.

Keep it:

* writable by the bot
* persistent across restarts
* exclusive to one running bot instance

Deleting the profile resets the saved browser session state. If that state is important, back it up together with your database.

## Tests

Install the development dependencies and run:

```bash
pip install -r requirements-dev.txt
pytest
```

The test suite covers input validation, EAES result parsing, HTML escaping, and asynchronous database operations using a temporary SQLite database.

No external services are required to run the tests.

## License

MIT — see [`LICENSE`](./LICENSE).
