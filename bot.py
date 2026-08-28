"""
Telegram Bot Application & Scheduler
====================================
Configures command handlers and manages automated polling loops.
"""

from __future__ import annotations

import asyncio
import logging
import os

import nodriver as uc
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from db import (
    add_tracking,
    async_session_factory,
    increment_attempts,
    init_db,
    list_active_tracking,
    list_chat_tracking,
    remove_tracking,
)
from scraper import (
    ADMISSION_NUMBER_RE,
    FIRST_NAME_RE,
    check_browser_health,
    fetch_eaes_result,
    parse_eaes_raw_text,
)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("Set TELEGRAM_BOT_TOKEN before launching.")

POLL_INTERVAL_SECONDS = int(os.environ.get("EAES_POLL_INTERVAL_SECONDS", "300"))
MAX_CONCURRENT_CHECKS = int(os.environ.get("EAES_MAX_CONCURRENT", "3"))
MAX_ATTEMPTS = int(os.environ.get("EAES_MAX_ATTEMPTS", "50"))
FETCH_TIMEOUT_SECONDS = int(os.environ.get("EAES_FETCH_TIMEOUT_SECONDS", "60"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("eaes_bot")


async def launch_browser() -> uc.Browser:
    """Launches or recovers nodriver browser instance."""
    return await uc.start(
        user_data_dir="./chrome_profile",
        headless=False,
    )


async def check_student(
    application: Application,
    browser: uc.Browser,
    semaphore: asyncio.Semaphore,
    chat_id: int,
    admission_number: str,
    first_name: str,
) -> None:
    async with semaphore:
        async with async_session_factory() as session:
            try:
                result = await asyncio.wait_for(
                    fetch_eaes_result(browser, admission_number, first_name),
                    timeout=FETCH_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                result = {"status": "error", "reason": "timeout"}

            status = result.get("status")

            if status == "success":
                msg = parse_eaes_raw_text(result.get("raw_text", ""))
                try:
                    await application.bot.send_message(
                        chat_id=chat_id, text=msg, parse_mode="MarkdownV2"
                    )
                    await remove_tracking(session, chat_id, admission_number)
                    logger.info("Delivered result to chat %s", chat_id)
                except TelegramError as e:
                    logger.warning("Telegram delivery failed for %s: %s", chat_id, e)
                return

            # Increment attempt count if parsing not completed
            attempts = await increment_attempts(session, chat_id, admission_number)
            if attempts >= MAX_ATTEMPTS:
                await remove_tracking(session, chat_id, admission_number)
                try:
                    await application.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"Stopped tracking admission `{admission_number}` after "
                            f"{MAX_ATTEMPTS} checks without a result. Please double-check "
                            "the details and re-add via /track."
                        ),
                    )
                except TelegramError:
                    pass


async def poll_cycle(context: ContextTypes.DEFAULT_TYPE) -> None:
    application: Application = context.application

    if application.bot_data.get("poll_running"):
        return

    application.bot_data["poll_running"] = True
    try:
        browser = application.bot_data.get("browser")

        # Browser Health Check & Relaunch Logic
        is_healthy = await check_browser_health(browser)
        if not is_healthy:
            logger.warning("Browser instance down. Attempting relaunch...")
            try:
                browser = await launch_browser()
                application.bot_data["browser"] = browser
            except Exception as e:
                logger.error("Failed to relaunch browser: %s. Skipping poll cycle.", e)
                return  # Skip cycle completely without incrementing attempt counter

        async with async_session_factory() as session:
            records = await list_active_tracking(session)

        if not records:
            return

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
        tasks = [
            check_student(
                application,
                browser,
                semaphore,
                r.chat_id,
                r.admission_number,
                r.first_name,
            )
            for r in records
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    finally:
        application.bot_data["poll_running"] = False


# Telegram Command Handlers
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "EAES Result Tracker\n\n"
        "/track <admission_number> <first_name> — Start tracking\n"
        "/status — View active tracked results\n"
        "/stop <admission_number> — Stop tracking"
    )


async def cmd_track(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: /track <admission_number> <first_name>")
        return

    admission_number = args[0].strip()
    first_name = " ".join(args[1:]).strip()

    if not ADMISSION_NUMBER_RE.match(admission_number):
        await update.message.reply_text("Invalid admission number format.")
        return
    if not FIRST_NAME_RE.match(first_name):
        await update.message.reply_text("Invalid first name format.")
        return

    async with async_session_factory() as session:
        outcome = await add_tracking(session, chat_id, admission_number, first_name)

    if outcome == "added":
        await update.message.reply_text(f"Tracking started for {admission_number}.")
    elif outcome == "exists":
        await update.message.reply_text("You are already tracking this number.")
    elif outcome == "limit_reached":
        await update.message.reply_text("Tracking limit reached (max 10).")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    async with async_session_factory() as session:
        records = await list_chat_tracking(session, chat_id)

    if not records:
        await update.message.reply_text("You aren't tracking any results.")
        return

    lines = ["Currently tracking:"]
    for r in records:
        lines.append(
            f"• {r.admission_number} ({r.first_name}) — Attempts: {r.attempts}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /stop <admission_number>")
        return

    admission_number = args[0].strip()
    async with async_session_factory() as session:
        removed = await remove_tracking(session, chat_id, admission_number)

    if removed:
        await update.message.reply_text(f"Stopped tracking {admission_number}.")
    else:
        await update.message.reply_text("Record not found.")


async def post_init(application: Application) -> None:
    await init_db()
    browser = await launch_browser()
    application.bot_data["browser"] = browser
    application.bot_data["poll_running"] = False
    logger.info("Bot initialized and nodriver session active.")


async def post_shutdown(application: Application) -> None:
    browser = application.bot_data.get("browser")
    if browser:
        try:
            browser.stop()
        except Exception:
            pass


def main() -> None:
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("track", cmd_track))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("stop", cmd_stop))

    application.job_queue.run_repeating(
        poll_cycle,
        interval=POLL_INTERVAL_SECONDS,
        first=10,
    )

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
