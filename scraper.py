"""
Web Scraping & DOM Parsing Module
=================================
Handles nodriver browser instances, Cloudflare Turnstile token checks,
DOM synthetic interaction, and output markdown formatting.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import Optional

import nodriver as uc

logger = logging.getLogger("eaes_scraper")

EAES_URL = "https://result.eaes.et/"

# Validation Patterns
ADMISSION_NUMBER_RE = re.compile(r"^[A-Za-z0-9/\-]{3,30}$")
FIRST_NAME_RE = re.compile(r"^[A-Za-z\s.\-]{2,60}$")

# Markdown Escaping Regular Expressions
_MDV2_RESERVED_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!])")
_CODE_SPAN_RESERVED_RE = re.compile(r"([`\\])")


def mask_identifier(value: str) -> str:
    """Masks admission numbers for privacy in logs."""
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def escape_md_v2(text: str) -> str:
    """Escapes Telegram MarkdownV2 reserved characters for structural text."""
    if text is None:
        return ""
    return _MDV2_RESERVED_RE.sub(r"\\\1", str(text))


def escape_code_span(text: str) -> str:
    """Escapes inline code span characters (`...`) - only backticks and backslashes."""
    if text is None:
        return ""
    return _CODE_SPAN_RESERVED_RE.sub(r"\\\1", str(text))


async def jitter_sleep(min_s: float = 0.5, max_s: float = 2.0) -> None:
    """Introduces random timing delay to avoid rigid execution signatures."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def check_browser_health(browser: Optional[uc.Browser]) -> bool:
    """Verifies that the nodriver browser process is alive and responsive."""
    if browser is None:
        return False
    try:
        page = await browser.get("about:blank", new_tab=True)
        await page.close()
        return True
    except Exception as e:
        logger.warning("Browser health check failed: %s", e)
        return False


async def fetch_eaes_result(
    browser: uc.Browser, admission_number: str, first_name: str
) -> dict:
    """Executes turnstile bypass and fetches examination result DOM text."""
    page = None
    try:
        page = await browser.get(EAES_URL, new_tab=True)
        await jitter_sleep(3.0, 5.0)

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
            return {"status": "error", "reason": "inputs_not_found"}

        await jitter_sleep(1.0, 2.0)

        # Poll for Cloudflare Turnstile completion
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
            return {"status": "blocked", "reason": "turnstile_timeout"}

        # Fire synthetic click events
        await page.evaluate("""
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

        await jitter_sleep(5.0, 7.0)

        raw_text = await page.evaluate("(() => document.body.innerText.trim())()")
        if not isinstance(raw_text, str) or not raw_text:
            return {"status": "error", "reason": "empty_extraction"}

        if "Enter credentials to view your exam results" in raw_text:
            return {"status": "pending"}

        if "TOTAL" in raw_text:
            return {"status": "success", "raw_text": raw_text}

        return {
            "status": "error",
            "reason": "unrecognized_state",
            "raw_text": raw_text[:300],
        }

    except Exception as e:
        logger.error(
            "Error checking admission %s: %s", mask_identifier(admission_number), e
        )
        return {"status": "error", "reason": "exception"}
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass


def parse_eaes_raw_text(raw_text: str) -> str:
    """Parses extracted DOM body text into a formatted Telegram MarkdownV2 message."""
    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]

    name, school = "Unknown", "Unknown"
    admission_no, stream, gender = "N/A", "N/A", "N/A"
    total_score, avg_score = "N/A", "N/A"
    subjects: list[tuple[str, str]] = []

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
            subjects.append((line, lines[i + 1]))

    try:
        adm_idx = lines.index("Admission No:")
        if adm_idx >= 2:
            name = lines[adm_idx - 2]
            school = lines[adm_idx - 1]
    except ValueError:
        pass

    msg_lines = [
        "🎓 *EAES Exam Result Found\\!*",
        "",
        f"👤 *Name:* `{escape_code_span(name)}`",
        f"🏫 *School:* `{escape_code_span(school)}`",
        f"🆔 *Admission No:* `{escape_code_span(admission_no)}`",
        f"📚 *Stream:* `{escape_code_span(stream)}` \\| *Sex:* `{escape_code_span(gender)}`",
        "",
        f"🏆 *TOTAL SCORE:* `{escape_code_span(total_score)}`",
        f"📈 *AVERAGE:* `{escape_code_span(avg_score)}`",
        "",
        "*📋 Subject Breakdown:*",
    ]

    for subj, score in subjects:
        msg_lines.append(f"• *{escape_md_v2(subj)}:* `{escape_code_span(score)}`")

    return "\n".join(msg_lines)
