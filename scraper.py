"""
All browser-automation and result-parsing logic for result.eaes.et.

This module owns everything that touches `nodriver` (a CDP-based Chrome
driver) plus the pure-text DOM parsing that turns a scraped page into a
Telegram message. bot.py should never import `nodriver` directly — it only
calls the functions below.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import random

import nodriver as uc

import config
from utils import escape_md_v2, mask

logger = logging.getLogger("eaes_bot.scraper")

_virtual_display = None  # populated by start_browser() when requested


async def _jitter_sleep(base_seconds: float) -> None:
    """Sleep base_seconds plus a small random jitter to avoid rigid timing signatures."""
    jitter = random.uniform(config.JITTER_MIN_SECONDS, config.JITTER_MAX_SECONDS)
    await asyncio.sleep(base_seconds + jitter)


# Browser lifecycle
async def start_browser() -> uc.Browser:
    """Starts (or restarts) the shared, persistent Chrome instance.

    Cross-platform notes:
      - On Windows/macOS, running with a real desktop session, no extra
        setup is needed — nodriver drives the normal, visible Chrome window.
      - On headless Linux (a server with no X session), Chrome still needs
        *some* display to render into for Turnstile to reliably pass. Either:
          (a) run the bot under an externally managed `xvfb-run` wrapper, or
          (b) set EAES_USE_VIRTUAL_DISPLAY=1 to have this function start a
              virtual display itself via `pyvirtualdisplay` (requires the
              `pyvirtualdisplay` pip package and the `xvfb` system package).
      This function never shells out to Xvfb directly, so nothing here is
      Linux-specific unless you opt into (b).
    """
    global _virtual_display

    if config.USE_VIRTUAL_DISPLAY and platform.system() == "Linux":
        try:
            from pyvirtualdisplay import Display

            _virtual_display = Display(visible=False, size=(1920, 1080))
            _virtual_display.start()
            logger.info("Virtual display started (pyvirtualdisplay).")
        except ImportError:
            logger.error(
                "EAES_USE_VIRTUAL_DISPLAY=1 but pyvirtualdisplay isn't installed. "
                "Install it with `pip install pyvirtualdisplay`, or run this "
                "process under `xvfb-run` instead."
            )
            raise

    kwargs = dict(
        user_data_dir=config.CHROME_PROFILE_DIR,
        headless=False,  # Turnstile is far more likely to pass in headed mode.
    )
    if config.CHROME_EXECUTABLE_PATH:
        kwargs["browser_executable_path"] = config.CHROME_EXECUTABLE_PATH

    browser = await uc.start(**kwargs)
    logger.info("nodriver browser started with persistent session profile.")
    return browser


def stop_browser(browser: uc.Browser | None) -> None:
    """Best-effort synchronous stop, safe to call with None or a dead browser."""
    global _virtual_display
    if browser is not None:
        try:
            browser.stop()
        except Exception:
            logger.exception("Error stopping nodriver browser")
    if _virtual_display is not None:
        try:
            _virtual_display.stop()
        except Exception:
            logger.exception("Error stopping virtual display")
        _virtual_display = None


async def is_browser_alive(
    browser: uc.Browser, timeout_seconds: int | None = None
) -> bool:
    """Lightweight liveness check: open a blank tab, run trivial JS, close it.

    Bounded by BROWSER_HEALTH_CHECK_TIMEOUT_SECONDS so a hung-but-not-dead
    browser process doesn't stall the health check itself.
    """
    timeout_seconds = timeout_seconds or config.BROWSER_HEALTH_CHECK_TIMEOUT_SECONDS
    page = None
    try:

        async def _probe() -> bool:
            nonlocal page
            page = await browser.get("about:blank", new_tab=True)
            result = await page.evaluate("1 + 1")
            return result == 2

        return await asyncio.wait_for(_probe(), timeout=timeout_seconds)
    except Exception as e:
        logger.warning("Browser health check failed: %s", e)
        return False
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass


# Result fetching
#
# fetch_eaes_result returns a dict with a "status" key so callers can tell apart
# a genuine "not published yet" response from a Cloudflare block or DOM/parse
# failure, instead of collapsing everything into None:
#   status == "success"  -> raw_text contains the rendered result payload
#   status == "pending"  -> page loaded fine, but result isn't published yet
#   status == "blocked"  -> Cloudflare Turnstile token never attached in time
#   status == "error"    -> button not found / DOM extraction failed / exception
async def fetch_eaes_result(
    browser: uc.Browser, admission_number: str, first_name: str
) -> dict:
    page = None
    try:
        page = await browser.get(config.EAES_URL, new_tab=True)
        await _jitter_sleep(4)

        # 1. Fill input values natively
        adm_json = json.dumps(str(admission_number))
        name_json = json.dumps(str(first_name))

        fill_js = f"""
        (() => {{
            const inputs = document.querySelectorAll("input[type='text'], input:not([type])");
            if (inputs.length < 2) return false;

            const setNativeValue = (el, val) => {{
                const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')?.set;
                if (setter) setter.call(el, val);
                else el.value = val;
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }};

            setNativeValue(inputs[0], {adm_json});
            setNativeValue(inputs[1], {name_json});
            return true;
        }})()
        """
        filled = await page.evaluate(fill_js)
        if not filled:
            logger.warning(
                "Could not locate admission/name inputs for %s", mask(admission_number)
            )
            return {"status": "error", "reason": "inputs_not_found"}

        await _jitter_sleep(1)

        # 2. Poll up to 20 seconds for Cloudflare Turnstile token
        logger.info(
            "Waiting for Cloudflare turnstile token to attach to parent form..."
        )
        token_ready = False
        for _ in range(20):
            has_token = await page.evaluate("""
            (() => {
                const tokenInput = document.querySelector('[name="cf-turnstile-response"], [name="g-recaptcha-response"]');
                return tokenInput && tokenInput.value.length > 0;
            })()
            """)
            if has_token:
                token_ready = True
                break
            await asyncio.sleep(1)

        logger.info("Turnstile token ready: %s", token_ready)

        if not token_ready:
            logger.warning(
                "Cloudflare Turnstile token failed to attach in time for %s",
                mask(admission_number),
            )
            return {"status": "blocked", "reason": "turnstile_timeout"}

        # 3. Fire full React synthetic event sequence (PointerDown -> MouseDown -> Click)
        logger.info(
            "Dispatching synthetic React click sequence to 'Check Result' button..."
        )
        clicked = await page.evaluate("""
        (() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const btn = btns.find(b => b.innerText.toLowerCase().includes('check')) || btns[0];
            if (!btn) return false;

            ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(eventType => {
                btn.dispatchEvent(new MouseEvent(eventType, {
                    view: window,
                    bubbles: true,
                    cancelable: true
                }));
            });
            return true;
        })()
        """)
        if not clicked:
            logger.warning(
                "Check-result button not found for %s", mask(admission_number)
            )
            return {"status": "error", "reason": "button_not_found"}

        # 4. Wait for Next.js routing / network render
        logger.info("Waiting for results page to render...")
        await _jitter_sleep(6)

        # 5. Extract DOM result
        extract_js = """
        (() => document.body.innerText.trim())()
        """
        raw_text = await page.evaluate(extract_js)
        if not isinstance(raw_text, str):
            raw_text = ""

        if not raw_text:
            logger.warning("Empty DOM extraction for %s", mask(admission_number))
            return {"status": "error", "reason": "empty_extraction"}

        if "Enter credentials to view your exam results" in raw_text:
            return {"status": "pending"}

        if "TOTAL" in raw_text:
            return {"status": "success", "raw_text": raw_text}

        # Page rendered something, but it doesn't match a known state.
        logger.warning("Unrecognized page state for %s", mask(admission_number))
        return {
            "status": "error",
            "reason": "unrecognized_page_state",
            "raw_text": raw_text[:500],
        }

    except Exception as e:
        logger.error(
            "Error executing fetch_eaes_result for %s: %s",
            mask(admission_number),
            e,
            exc_info=True,
        )
        return {"status": "error", "reason": "exception"}

    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                logger.exception("Failed to close tab for %s", mask(admission_number))


def parse_eaes_raw_text(raw_text: str) -> str:
    """Parses raw extracted DOM text from EAES into a clean Telegram Markdown message."""
    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]

    # Defaults
    name = "Unknown"
    school = "Unknown"
    admission_no = "N/A"
    stream = "N/A"
    gender = "N/A"
    total_score = "N/A"
    avg_score = "N/A"
    subjects: list[tuple[str, str]] = []

    # Known subject labels on EAES
    known_subjects = {
        "English",
        "Mathematics",
        "Scholastic Aptitude Test",
        "Physics",
        "Chemistry",
        "Biology",
        "History",
        "Economics",
        "Geography",
    }

    # Extract Header Data
    for i, line in enumerate(lines):
        if line == "Admission No:" and i + 1 < len(lines):
            admission_no = lines[i + 1]
        elif line == "Stream:" and i + 1 < len(lines):
            stream = lines[i + 1]
        elif line == "Sex:" and i + 1 < len(lines):
            gender = lines[i + 1]
        elif line == "TOTAL" and i + 1 < len(lines):
            total_score = lines[i + 1]
        elif line == "AVG" and i + 1 < len(lines):
            avg_score = lines[i + 1]
        elif line in known_subjects and i + 1 < len(lines):
            score = lines[i + 1]
            subjects.append((line, score))

    # Name and School usually appear right above Admission No
    try:
        adm_index = lines.index("Admission No:")
        if adm_index >= 2:
            name = lines[adm_index - 2]
            school = lines[adm_index - 1]
    except ValueError:
        pass

    # Build Telegram Output (MarkdownV2)
    #
    # All dynamic values (name, school, admission_no, stream, gender, scores,
    # subject names) are parsed straight from the DOM and can contain
    # characters MarkdownV2 treats as formatting syntax, so every one of them
    # is passed through escape_md_v2 before being embedded. Static label
    # text with reserved characters (e.g. "!", ".") is escaped inline too.
    msg_lines = [
        "🎓 *EAES Exam Result Found\\!*",
        "",
        f"👤 *Name:* `{escape_md_v2(name)}`",
        f"🏫 *School:* `{escape_md_v2(school)}`",
        f"🆔 *Admission No:* `{escape_md_v2(admission_no)}`",
        f"📚 *Stream:* `{escape_md_v2(stream)}` \\| *Sex:* `{escape_md_v2(gender)}`",
        "",
        f"🏆 *TOTAL SCORE:* `{escape_md_v2(total_score)}`",
        f"📈 *AVERAGE:* `{escape_md_v2(avg_score)}`",
        "",
        "*📋 Subject Breakdown:*",
    ]

    for subj, score in subjects:
        msg_lines.append(f"• *{escape_md_v2(subj)}:* `{escape_md_v2(score)}`")

    return "\n".join(msg_lines)
