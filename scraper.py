"""Browser automation and result parsing for result.eaes.et.

Owns everything that touches `nodriver` plus the DOM parsing that turns a
scraped page into a Telegram message. bot.py never imports nodriver
directly — it only calls the functions below.
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

_virtual_display = None


async def _jitter_sleep(base_seconds: float) -> None:
    jitter = random.uniform(config.JITTER_MIN_SECONDS, config.JITTER_MAX_SECONDS)
    await asyncio.sleep(base_seconds + jitter)


async def start_browser() -> uc.Browser:
    """Starts (or restarts) the shared, persistent Chrome instance."""
    global _virtual_display

    if config.USE_VIRTUAL_DISPLAY and platform.system() == "Linux":
        try:
            from pyvirtualdisplay import Display

            _virtual_display = Display(visible=False, size=(1920, 1080))
            _virtual_display.start()
            logger.info("Virtual display started.")
        except ImportError:
            logger.error("EAES_USE_VIRTUAL_DISPLAY=1 but pyvirtualdisplay isn't installed.")
            raise

    kwargs = {
    "user_data_dir": config.CHROME_PROFILE_DIR,
    "headless": False,  # Turnstile is far more likely to pass in headed mode
}
    if config.CHROME_EXECUTABLE_PATH:
        kwargs["browser_executable_path"] = config.CHROME_EXECUTABLE_PATH

    browser = await uc.start(**kwargs)
    logger.info("nodriver browser started.")
    return browser


def stop_browser(browser: uc.Browser | None) -> None:
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


async def is_browser_alive(browser: uc.Browser, timeout_seconds: int | None = None) -> bool:
    """Opens a blank tab and runs trivial JS to check the browser is responsive."""
    timeout_seconds = timeout_seconds or config.BROWSER_HEALTH_CHECK_TIMEOUT_SECONDS
    page = None
    try:

        async def _probe() -> bool:
            nonlocal page
            page = await browser.get("about:blank", new_tab=True)
            return await page.evaluate("1 + 1") == 2

        return await asyncio.wait_for(_probe(), timeout=timeout_seconds)
    except Exception as e:  # noqa: BLE001
        logger.warning("Browser health check failed: %s", e)
        return False
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("Failed to close health-check page: %s", e)


# status: "success" (raw_text has the result), "pending" (not published yet),
# "blocked" (Turnstile token never attached), "error" (DOM/parse failure)
async def fetch_eaes_result(browser: uc.Browser, admission_number: str, first_name: str) -> dict:
    page = None
    try:
        page = await browser.get(config.EAES_URL, new_tab=True)
        await _jitter_sleep(4)

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
            logger.warning("Could not locate admission/name inputs for %s", mask(admission_number))
            return {"status": "error", "reason": "inputs_not_found"}

        await _jitter_sleep(1)

        # Poll up to 20s for the Cloudflare Turnstile token to attach.
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

        if not token_ready:
            logger.warning("Turnstile token failed to attach in time for %s", mask(admission_number))
            return {"status": "blocked", "reason": "turnstile_timeout"}

        clicked = await page.evaluate("""
        (() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const btn = btns.find(b => b.innerText.toLowerCase().includes('check')) || btns[0];
            if (!btn) return false;

            ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(eventType => {
                btn.dispatchEvent(new MouseEvent(eventType, { view: window, bubbles: true, cancelable: true }));
            });
            return true;
        })()
        """)
        if not clicked:
            logger.warning("Check-result button not found for %s", mask(admission_number))
            return {"status": "error", "reason": "button_not_found"}

        await _jitter_sleep(6)

        raw_text = await page.evaluate("(() => document.body.innerText.trim())()")
        if not isinstance(raw_text, str) or not raw_text:
            logger.warning("Empty DOM extraction for %s", mask(admission_number))
            return {"status": "error", "reason": "empty_extraction"}

        if "Enter credentials to view your exam results" in raw_text:
            return {"status": "pending"}

        if "TOTAL" in raw_text:
            return {"status": "success", "raw_text": raw_text}

        logger.warning("Unrecognized page state for %s", mask(admission_number))
        return {"status": "error", "reason": "unrecognized_page_state", "raw_text": raw_text[:500]}

    except Exception as e:  # noqa: BLE001
        logger.error("Error fetching result for %s: %s", mask(admission_number), e)
        return {"status": "error", "reason": "exception"}

    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                logger.exception("Failed to close tab for %s", mask(admission_number))


KNOWN_SUBJECTS = {
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

SUBJECT_EMOJIS = {
    "English": "📖",
    "Mathematics": "🔢",
    "Scholastic Aptitude Test": "🧠",
    "Physics": "⚛️",
    "Chemistry": "🧪",
    "Biology": "🧬",
    "History": "📜",
    "Economics": "💰",
    "Geography": "🌍",
}


def parse_eaes_raw_text(raw_text: str) -> str:
    """Parses raw extracted DOM text from EAES into a MarkdownV2 Telegram message."""
    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]

    name = "Unknown"
    school = "Unknown"
    admission_no = "N/A"
    stream = "N/A"
    gender = "N/A"
    total_score = "N/A"
    avg_score = "N/A"
    subjects: list[tuple[str, str]] = []

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
        elif line in KNOWN_SUBJECTS and i + 1 < len(lines):
            subjects.append((line, lines[i + 1]))

    try:
        adm_index = lines.index("Admission No:")
        if adm_index >= 2:
            name = lines[adm_index - 2]
            school = lines[adm_index - 1]
    except ValueError:
        pass

    # Every value below comes straight from the DOM, so it's run through
    # escape_md_v2 before being embedded in the MarkdownV2 message.
    msg_lines = [
        "🎓 *EAES Exam Result*",
        "",
        f"👤 *Name:* `{escape_md_v2(name)}`",
        f"🏫 *School:* `{escape_md_v2(school)}`",
        f"🆔 *Admission No:* `{escape_md_v2(admission_no)}`",
        f"📚 *Stream:* `{escape_md_v2(stream)}` \\| *Sex:* `{escape_md_v2(gender)}`",
        "",
        f"🏆 *Total Score:* `{escape_md_v2(total_score)}`",
        f"📈 *Average:* `{escape_md_v2(avg_score)}`",
        "",
        "*📋 Subject Breakdown:*",
    ]
    for subj, score in subjects:
        emoji = SUBJECT_EMOJIS.get(subj, "•")
        msg_lines.append(f"{emoji} *{escape_md_v2(subj)}:* `{escape_md_v2(score)}`")

    return "\n".join(msg_lines)