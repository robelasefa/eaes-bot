"""
EUEE Result Tracking Telegram Bot
==================================

Polls result.eaes.et for a set of tracked students using nodriver to bypass
Cloudflare Turnstile checks and delivers results via Telegram as soon as
they're published.

This module only handles Telegram commands, scheduling, and lifecycle.
Database access lives in db.py; all browser automation lives in scraper.py.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import config
import db
import scraper
from utils import (
    ADMISSION_NUMBER_RE,
    FIRST_NAME_RE,
    build_stop_callback_data,
    mask,
    parse_stop_callback_data,
)

if not config.BOT_TOKEN:
    raise SystemExit("Set the TELEGRAM_BOT_TOKEN environment variable before running.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("eaes_bot")


# Per-student check
async def check_one_student(
    application: Application,
    browser,
    semaphore: asyncio.Semaphore,
    row: db.Tracking,
) -> None:
    """Checks a single tracked student and reacts to the outcome.

    On a confirmed result: sends the formatted message and stops tracking
    the entry. On anything else (pending, blocked, error, or an unexpected
    exception): increments the attempt counter via
    `_record_attempt_and_maybe_drop`, which auto-drops and notifies the user
    once `config.MAX_ATTEMPTS` is reached. Runs under `semaphore` so at most
    `config.MAX_CONCURRENT_CHECKS` browser tabs are open at once.
    """
    chat_id = row.chat_id
    admission_number = row.admission_number
    first_name = row.first_name

    # 1. Validate inputs
    if not ADMISSION_NUMBER_RE.match(admission_number or "") or not FIRST_NAME_RE.match(
        first_name or ""
    ):
        logger.warning(
            "Skipping malformed tracking record for chat %s (admission=%s)",
            chat_id,
            mask(admission_number or ""),
        )
        return

    # 2. Limit concurrent browser operations
    async with semaphore:
        try:
            logger.info(
                "Checking results for admission=%s (chat=%s)",
                mask(admission_number),
                chat_id,
            )

            # 3. Fetch result via nodriver, with a hard timeout so a hung page
            #    can never hold the semaphore slot (and the poll cycle) forever.
            try:
                result = await asyncio.wait_for(
                    scraper.fetch_eaes_result(browser, admission_number, first_name),
                    timeout=config.FETCH_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Fetch timed out after %ss for admission=%s (chat=%s)",
                    config.FETCH_TIMEOUT_SECONDS,
                    mask(admission_number),
                    chat_id,
                )
                result = {"status": "error", "reason": "timeout"}

            status = result.get("status", "error")

            if status == "success":
                raw_text = result.get("raw_text", "")
                message_text = scraper.parse_eaes_raw_text(raw_text)
                try:
                    await application.bot.send_message(
                        chat_id=chat_id, text=message_text, parse_mode="MarkdownV2"
                    )
                    logger.info(
                        "Delivered result to chat %s (admission=%s)",
                        chat_id,
                        mask(admission_number),
                    )
                    await db.delete_tracking(chat_id, admission_number)
                    logger.info(
                        "Cleared tracking for chat %s (admission=%s)",
                        chat_id,
                        mask(admission_number),
                    )
                except TelegramError as e:
                    # Delivery failed (user blocked bot, chat gone, etc). Don't
                    # silently orphan the row forever - let the attempt ceiling
                    # below eventually catch and drop it.
                    logger.warning(
                        "Telegram delivery failed for chat %s (admission=%s): %s",
                        chat_id,
                        mask(admission_number),
                        e,
                    )
                    await _record_attempt_and_maybe_drop(
                        application, chat_id, admission_number
                    )
                return

            if status == "pending":
                logger.info("Result not yet published for %s", mask(admission_number))
            elif status == "blocked":
                logger.warning(
                    "Cloudflare/Turnstile blocked check for admission=%s (chat=%s): %s",
                    mask(admission_number),
                    chat_id,
                    result.get("reason"),
                )
            else:
                logger.warning(
                    "Check error for admission=%s (chat=%s): %s",
                    mask(admission_number),
                    chat_id,
                    result.get("reason", "unknown"),
                )

            await _record_attempt_and_maybe_drop(application, chat_id, admission_number)

        except Exception as e:
            logger.error(
                "Unexpected error checking chat %s (admission=%s): %s",
                chat_id,
                mask(admission_number),
                e,
                exc_info=True,
            )
            await _record_attempt_and_maybe_drop(application, chat_id, admission_number)


async def _record_attempt_and_maybe_drop(
    application: Application, chat_id: int, admission_number: str
) -> None:
    """Increments the attempt counter and drops + notifies once MAX_ATTEMPTS is hit."""
    attempts = await db.increment_attempts(chat_id, admission_number)
    if attempts < config.MAX_ATTEMPTS:
        return

    logger.warning(
        "Attempt ceiling (%d) reached for admission=%s (chat=%s); dropping from tracking.",
        config.MAX_ATTEMPTS,
        mask(admission_number),
        chat_id,
    )
    await db.delete_tracking(chat_id, admission_number)
    try:
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                f"I've stopped tracking admission number {admission_number} after "
                f"{config.MAX_ATTEMPTS} checks with no result. This usually means the "
                "admission number or first name doesn't match what's on file, or "
                "the site is temporarily blocking automated checks. You can restart "
                "tracking with /track once you've double-checked the details."
            ),
        )
    except TelegramError as e:
        logger.warning(
            "Could not notify chat %s about dropped tracking: %s", chat_id, e
        )


# Poll cycle (runs on a schedule)
async def _relaunch_browser(application: Application):
    """Attempts to stop the dead browser (best effort) and start a fresh one."""
    old_browser = application.bot_data.get("browser")
    scraper.stop_browser(old_browser)

    try:
        new_browser = await scraper.start_browser()
        application.bot_data["browser"] = new_browser
        logger.info("Browser relaunched successfully.")
        return new_browser
    except Exception:
        logger.exception("Failed to relaunch browser")
        return None


async def poll_cycle(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job (`job_queue.run_repeating`): checks every active tracking row.

    Guarded so overlapping runs never share the single Chrome instance, and
    so a crashed/hung browser causes a relaunch-and-skip rather than every
    tracked student getting an attempt penalized for an infra problem.
    """
    application: Application = context.application
    browser = application.bot_data.get("browser")
    if browser is None:
        logger.error("Poll cycle skipped: browser is not initialized.")
        return

    # Overlap guard: skip this run entirely if the previous poll cycle is
    # still in flight (e.g. it's running long due to slow pages). Without
    # this, job_queue would fire concurrent cycles against the same shared
    # browser instance.
    if application.bot_data.get("poll_running"):
        logger.warning(
            "Previous poll cycle still running; skipping this scheduled run."
        )
        return

    application.bot_data["poll_running"] = True
    try:
        # Health check first. If the shared browser has crashed or hung at
        # the OS level, every student check this cycle would fail for a
        # reason that has nothing to do with their tracked data - so on a
        # failed health check we relaunch and skip the cycle entirely
        # WITHOUT touching any student's attempt counter. Blaming tracked
        # students' attempts on host/infra failure is exactly what caused
        # entries to get wrongly auto-dropped.
        if not await scraper.is_browser_alive(browser):
            logger.warning("Shared browser appears unresponsive; attempting relaunch.")
            browser = await _relaunch_browser(application)
            if browser is None:
                logger.error("Browser relaunch failed; skipping this poll cycle.")
            else:
                logger.info(
                    "Skipping this poll cycle after relaunch; will resume next cycle."
                )
            return

        rows = await db.list_active()
        if not rows:
            return

        logger.info("Polling %d active tracking record(s)", len(rows))
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_CHECKS)
        tasks = [
            check_one_student(application, browser, semaphore, row) for row in rows
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        application.bot_data["poll_running"] = False


# Telegram command handlers
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /start (and doubles as /help): shows a short command summary."""
    await update.message.reply_text(
        "EAES Result Tracker\n\n"
        "/track <admission_number> <first_name> — start tracking a result\n"
        "/status — show what you're currently tracking\n"
        "/stop <admission_number> — stop tracking\n\n"
        f"I check for new results roughly every {config.POLL_INTERVAL_SECONDS // 60} minute(s) "
        "and will message you the moment yours is published."
    )


async def cmd_track(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /track <admission_number> <first_name>: registers a new entry."""
    chat_id = update.effective_chat.id
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /track <admission_number> <first_name>\nExample: /track 88256644 Hirut"
        )
        return

    admission_number = args[0].strip()
    first_name = " ".join(args[1:]).strip()

    if not ADMISSION_NUMBER_RE.match(admission_number):
        await update.message.reply_text(
            "That admission number doesn't look valid. Please check it and try again."
        )
        return
    if not FIRST_NAME_RE.match(first_name):
        await update.message.reply_text(
            "That first name doesn't look valid. Please check it and try again."
        )
        return

    outcome = await db.add_tracking(chat_id, admission_number, first_name)
    if outcome == "added":
        await update.message.reply_text(
            f"Got it, {first_name}! I'll keep an eye out and message you here "
            "the moment your result is published."
        )
    elif outcome == "exists":
        await update.message.reply_text(
            "You're already tracking that admission number."
        )
    elif outcome == "limit_reached":
        await update.message.reply_text(
            f"You're already tracking the maximum of {config.MAX_TRACKED_PER_CHAT} results. "
            "Use /stop on one first if you'd like to add another."
        )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /status: lists tracked entries, each with an inline Stop button."""
    chat_id = update.effective_chat.id
    rows = await db.list_for_chat(chat_id)
    if not rows:
        await update.message.reply_text("You're not tracking any results right now.")
        return

    lines = ["Currently tracking:"]
    keyboard_rows = []
    for row in rows:
        lines.append(
            f"• {row.admission_number} ({row.first_name}) — "
            f"{row.status} ({row.attempts}/{config.MAX_ATTEMPTS} attempts)"
        )
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    f"Stop {row.admission_number}",
                    callback_data=build_stop_callback_data(row.admission_number),
                )
            ]
        )

    await update.message.reply_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard_rows)
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /stop <admission_number>: removes one tracked entry by typed command."""
    chat_id = update.effective_chat.id
    args = context.args or []
    if len(args) != 1:
        await update.message.reply_text("Usage: /stop <admission_number>")
        return

    admission_number = args[0].strip()
    removed = await db.remove_tracking(chat_id, admission_number)
    if removed:
        await update.message.reply_text(f"Stopped tracking {admission_number}.")
    else:
        await update.message.reply_text("You weren't tracking that admission number.")


async def on_stop_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles taps on the inline "Stop <admission_number>" button from /status.

    Same effect as /stop, reached via a tap instead of typing the admission
    number back in. Always answers the callback query first so Telegram
    clears the button's loading spinner even if the removal itself fails.
    """
    query = update.callback_query
    await query.answer()

    admission_number = parse_stop_callback_data(query.data)
    if admission_number is None:
        return

    chat_id = update.effective_chat.id
    removed = await db.remove_tracking(chat_id, admission_number)
    text = (
        f"Stopped tracking {admission_number}."
        if removed
        else "That entry is no longer being tracked."
    )
    await query.edit_message_text(text)


# Application lifecycle
async def post_init(application: Application) -> None:
    """Runs once on startup: initializes the DB, launches Chrome, sets the command menu."""
    await db.init_db()
    application.bot_data["poll_running"] = False
    application.bot_data["browser"] = await scraper.start_browser()

    # Populates Telegram's built-in "/" command autocomplete menu in clients.
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Show usage instructions"),
            BotCommand("help", "Show usage instructions"),
            BotCommand("track", "Track a new admission number"),
            BotCommand("status", "List what you're tracking"),
            BotCommand("stop", "Stop tracking an admission number"),
        ]
    )


async def post_shutdown(application: Application) -> None:
    """Runs once on shutdown: stops Chrome and closes the DB connection pool."""
    scraper.stop_browser(application.bot_data.get("browser"))
    await db.close_db()
    logger.info("Shutdown complete.")


async def error_handler(update, context):
    """Global error handler registered via `Application.add_error_handler`."""
    logger.error("Exception while handling an update:", exc_info=context.error)


# Entry point
def main() -> None:
    """Builds the Application, registers handlers and the poll job, and starts polling."""
    application = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler(["start", "help"], cmd_start))
    application.add_handler(CommandHandler("track", cmd_track))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("stop", cmd_stop))
    application.add_handler(CallbackQueryHandler(on_stop_button, pattern="^stop:"))
    application.add_error_handler(error_handler)

    application.job_queue.run_repeating(
        poll_cycle,
        interval=config.POLL_INTERVAL_SECONDS,
        first=10,
        name="eaes_poll_cycle",
    )

    logger.info(
        "Starting bot (poll interval=%ss, max_attempts=%s, fetch_timeout=%ss, db=%s)",
        config.POLL_INTERVAL_SECONDS,
        config.MAX_ATTEMPTS,
        config.FETCH_TIMEOUT_SECONDS,
        config.DATABASE_URL,
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
