"""EUEE Result Tracking Telegram Bot

Polls result.eaes.et for tracked students using nodriver to get past
Cloudflare Turnstile, and delivers results via Telegram as soon as they're
published.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import config
import db
import scraper
from utils import (
    ADMISSION_NUMBER_RE,
    FIRST_NAME_RE,
    build_stop_callback_data,
    escape_md_v2,
    mask,
    parse_stop_callback_data,
)

if not config.BOT_TOKEN:
    raise SystemExit("Set the TELEGRAM_BOT_TOKEN environment variable before running.")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("eaes_bot")


async def check_one_student(
    application: Application, browser, semaphore: asyncio.Semaphore, row: db.Tracking
) -> None:
    """Checks a single tracked student and reacts to the outcome.

    On a confirmed result, sends the message and stops tracking. Anything
    else (pending, blocked, error, or an unexpected exception) increments
    the attempt counter, which auto-drops the entry once MAX_ATTEMPTS is
    hit. Runs under `semaphore` so only MAX_CONCURRENT_CHECKS tabs are open
    at once.
    """
    chat_id = row.chat_id
    admission_number = row.admission_number
    first_name = row.first_name

    if not ADMISSION_NUMBER_RE.match(admission_number or "") or not FIRST_NAME_RE.match(
        first_name or ""
    ):
        logger.warning(
            "Skipping malformed tracking record for chat %s (admission=%s)",
            chat_id,
            mask(admission_number or ""),
        )
        return

    async with semaphore:
        try:
            logger.info(
                "Checking results for admission=%s (chat=%s)",
                mask(admission_number),
                chat_id,
            )

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
                message_text = scraper.parse_eaes_raw_text(result.get("raw_text", ""))
                try:
                    await application.bot.send_message(
                        chat_id=chat_id,
                        text=message_text,
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                    logger.info(
                        "Delivered result to chat %s (admission=%s)",
                        chat_id,
                        mask(admission_number),
                    )
                    await db.delete_tracking(chat_id, admission_number)
                except TelegramError as e:
                    # User blocked the bot, chat gone, etc. Don't orphan the
                    # row forever - let the attempt ceiling below catch it.
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

        except Exception as e:  # noqa: BLE001
            logger.error(
                "Unexpected error checking chat %s (admission=%s): %s",
                chat_id,
                mask(admission_number),
                e,
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
        "Attempt ceiling (%d) reached for admission=%s (chat=%s); dropping.",
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


async def _relaunch_browser(application: Application):
    """Stops the dead browser (best effort) and starts a fresh one."""
    scraper.stop_browser(application.bot_data.get("browser"))
    try:
        new_browser = await scraper.start_browser()
        application.bot_data["browser"] = new_browser
        logger.info("Browser relaunched successfully.")
        return new_browser
    except Exception:
        logger.exception("Failed to relaunch browser")
        return None


async def poll_cycle(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: checks every active tracking row.

    Guarded so overlapping runs never share the single Chrome instance, and
    a crashed/hung browser causes a relaunch-and-skip rather than every
    tracked student eating an attempt for an infra problem.
    """
    application: Application = context.application
    browser = application.bot_data.get("browser")
    if browser is None:
        logger.error("Poll cycle skipped: browser is not initialized.")
        return

    if application.bot_data.get("poll_running"):
        logger.warning(
            "Previous poll cycle still running; skipping this scheduled run."
        )
        return

    application.bot_data["poll_running"] = True
    try:
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


async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*Welcome to Ethio Entrance Checker\\!*\n\n"
        f"I’ll automatically check for your Grade 12 results every ~{config.POLL_INTERVAL_SECONDS // 60} minutes "
        "and let you know as soon as they’re available\\.\n\n"
        "📌 *Available commands:*\n"
        "• `/track <admission_no> <first_name>` — Start tracking your result\n"
        "• `/status` — See your tracked results\n"
        "• `/stop <admission_no>` — Stop tracking a result\n\n"
        "Good luck with your results\\! 🎉",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def cmd_track(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = context.args or []

    if len(args) < 2:
        await update.message.reply_text(
            "*How to track a result*\n\n"
            "Send the command like this:\n"
            "`/track <admission_number> <first_name>`\n\n"
            "Example:\n"
            "/track 88256644 Hirut",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    admission_number = args[0].strip()
    first_name = " ".join(args[1:]).strip()

    if not ADMISSION_NUMBER_RE.match(admission_number):
        await update.message.reply_text(
            "That admission number doesn't look right. "
            "Please double-check it and try again."
        )
        return

    if not FIRST_NAME_RE.match(first_name):
        await update.message.reply_text(
            "That name doesn't look right. "
            "Please enter the first name exactly as it appears on the exam registration."
        )
        return

    outcome = await db.add_tracking(chat_id, admission_number, first_name)

    if outcome == "added":
        safe_name = escape_md_v2(first_name)
        safe_admission = escape_md_v2(admission_number)

        await update.message.reply_text(
            "✅ *You're all set\\!*\n\n"
            f"I'm now tracking the result for *{safe_name}* "
            f"\\(`{safe_admission}`\\)\\. "
            "I'll message you as soon as the result is available\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    elif outcome == "exists":
        await update.message.reply_text(
            "You're already tracking that admission number. No need to add it again."
        )

    elif outcome == "limit_reached":
        await update.message.reply_text(
            f"You've reached the limit of {config.MAX_TRACKED_PER_CHAT} tracked results.\n\n"
            "Stop tracking one of them with /stop, then you can add another."
        )


async def cmd_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    rows = await db.list_for_chat(chat_id)

    if not rows:
        await update.message.reply_text(
            "📭 You're not tracking any results yet.\n\nUse /track to add one."
        )
        return

    lines = ["📋 *Your tracked results*", ""]

    keyboard_rows = []

    for row in rows:
        safe_name = escape_md_v2(row.first_name)
        safe_admission = escape_md_v2(row.admission_number)
        safe_status = escape_md_v2(row.status.replace("_", " ").title())

        lines.extend(
            [
                f"👤 *{safe_name}*",
                f"🎫 `{safe_admission}`",
                f"Status: *{safe_status}*",
                f"Checks: {row.attempts}/{config.MAX_ATTEMPTS}",
                "",
            ]
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
        "\n".join(lines).rstrip(),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup(keyboard_rows),
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = context.args or []

    if len(args) != 1:
        await update.message.reply_text(
            "*How to stop tracking*\n\n"
            "Send the command like this:\n"
            "/stop <admission_number\\>\n\n"
            "Example:\n"
            "`/stop 88256644`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    admission_number = args[0].strip()
    removed = await db.remove_tracking(chat_id, admission_number)

    if removed:
        await update.message.reply_text(f"✅ Stopped tracking {admission_number}.")
    else:
        await update.message.reply_text(
            "I couldn't find that admission number in your tracked results."
        )


async def on_stop_button(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles taps on the inline 'Stop <admission_number>' button from /status."""
    query = update.callback_query
    await query.answer()

    admission_number = parse_stop_callback_data(query.data)
    if admission_number is None:
        return

    chat_id = update.effective_chat.id
    removed = await db.remove_tracking(chat_id, admission_number)

    if removed:
        text = f"✅ Stopped tracking {admission_number}."
    else:
        text = "That result is no longer being tracked."

    await query.edit_message_text(text)


async def post_init(application: Application) -> None:
    await db.init_db()
    application.bot_data["poll_running"] = False
    application.bot_data["browser"] = await scraper.start_browser()

    await application.bot.set_my_commands(
        [
            BotCommand("start", "Show usage instructions"),
            BotCommand("track", "Track a new admission number"),
            BotCommand("status", "List what you're tracking"),
            BotCommand("stop", "Stop tracking an admission number"),
        ]
    )


async def post_shutdown(application: Application) -> None:
    scraper.stop_browser(application.bot_data.get("browser"))
    await db.close_db()
    logger.info("Shutdown complete.")


async def error_handler(_, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)


def main() -> None:
    application = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", cmd_start))
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
