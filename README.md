# EAES Result Tracker Bot

A Telegram bot that tracks Ethiopian Secondary School Leaving Examination (EAES) results and notifies students when their results are available.

## Features

* Track results with `/track`
* View active tracking with `/status`
* Stop tracking with `/stop`
* Automatic background polling
* Async SQLite and PostgreSQL support
* Persistent Chrome session with `nodriver`
* Cloudflare Turnstile token detection and automatic form submission
* Browser health checks and recovery
* Telegram MarkdownV2-safe result formatting

## Architecture

```text
eaes-bot/
├── bot.py
├── db.py
├── scraper.py
├── tests/
│   └── test_bot.py
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

The main flow is:

```text
Telegram
   ↓
bot.py
   ├── db.py        → Tracking database
   └── scraper.py   → EAES browser/session
                         ↓
                    Result parsing
```

## Requirements

* Python 3.11+
* Chrome or Chromium
* `uv`
* Telegram bot token

## Setup

Clone the repository:

```bash
git clone https://github.com/robelasefa/eaes-bot.git
cd eaes-bot
```

Activate your existing virtual environment, then install dependencies with `uv`:

```bash
uv pip install -r requirements.txt
```

Or install them directly:

```bash
uv pip install python-telegram-bot[job-queue] nodriver SQLAlchemy aiosqlite asyncpg
```

Create your environment file:

```bash
cp .env.example .env
```

Then set your Telegram bot token and database URL.

## Configuration

The active environment variables are:

```env
TELEGRAM_BOT_TOKEN=your-token
DATABASE_URL=sqlite+aiosqlite:///tracking.db

EAES_POLL_INTERVAL_SECONDS=300
EAES_MAX_CONCURRENT=3
EAES_MAX_ATTEMPTS=50
EAES_FETCH_TIMEOUT_SECONDS=60
```

You can also use PostgreSQL for production instead of SQLite:

```env
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/eaes
```

## Running

Start the bot with:

```bash
python bot.py
```

## Commands

```text
/start
/track <admission_number> <first_name>
/status
/stop <admission_number>
```

Example:

```text
/track 0045781312 Yabira
```

## Browser Session

The bot uses `nodriver` to control a persistent Chrome session.

The scraper checks the EAES page for the Cloudflare Turnstile response field:

```text
cf-turnstile-response
```

Once the Turnstile token is available, the scraper automatically triggers the result form submission and continues with DOM extraction and parsing.

If a valid token is not available within the configured wait period, the check is treated as blocked and retried during a later polling cycle.

## Database

The project uses SQLAlchemy 2.0's async API.

SQLite is the default:

```text
sqlite+aiosqlite:///tracking.db
```

PostgreSQL is also supported:

```text
postgresql+asyncpg://user:password@host:5432/eaes
```

## Testing

Run the test suite with:

```bash
uv run pytest -q
```

Tests use synthetic data only and an isolated in-memory database.
