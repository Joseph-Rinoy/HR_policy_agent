"""Headless Playwright automation that files a leave request on sumHR.

This is the importable, side-effect-free core: it takes everything as arguments
and returns a result dict (never raises, never prints, never calls input()). The
UI supplies the values and shows the returned message.

THREADING: Playwright's *sync* API must run on a thread with no asyncio loop and
must NOT be the Qt GUI thread. Call this only from inside ``LeaveWorker.run()``
(a QThread) -- calling it directly from a slot would freeze the UI for the whole
browser session and can deadlock with Qt's event loop.
"""

from __future__ import annotations

import os
from datetime import date

from paths import app_base_dir

# When packaged (v2), a Chromium build is bundled next to the exe in
# ``ms-playwright/``; point Playwright at it before it launches. In development
# this folder doesn't exist, so we fall back to the default user cache. Harmless
# either way thanks to setdefault.
_bundled_browsers = app_base_dir() / "ms-playwright"
if _bundled_browsers.exists():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_bundled_browsers))

from playwright.sync_api import sync_playwright

LOGIN_URL = "https://qubiqon.sumhr.io/login"

# The exact option labels in the sumHR "leave type" dropdown.
LEAVE_TYPES = [
    "Casual Leave",
    "Earned Leave",
    "Leave without Pay",
    "Optional Holiday",
    "Sick Leave",
    "Compoff Leave",
]


def apply_leave(
    creds: tuple[str, str],
    leave_type: str,
    leave_date: date,
    reason: str,
    *,
    submit: bool = False,
    headless: bool = True,
    slow_mo: int = 0,
) -> dict:
    """File a single-day, full-day leave request on sumHR.

    Args:
        creds: ``(username, password)`` for the sumHR login.
        leave_type: one of ``LEAVE_TYPES`` (exact dropdown text).
        leave_date: the day to apply for (a ``datetime.date``).
        reason: free-text reason.
        submit: if False (default), fill the form but do NOT click the final
            "Apply Leave" -- a safe dry-run for testing. If True, actually submit.
        headless: run without a visible browser window (default True).
        slow_mo: per-action delay in ms, useful when watching a headed run.

    Returns:
        ``{"ok": True, "message": ...}`` on success, or
        ``{"ok": False, "error": ...}`` on any failure. Never raises.
    """
    username, password = creds

    # v1 supports only the currently-shown month (no calendar month navigation).
    today = date.today()
    if (leave_date.year, leave_date.month) != (today.year, today.month):
        return {
            "ok": False,
            "error": "For now I can only pick a date in the current month.",
        }

    # The MUI calendar labels each day button like "June 9, 2026".
    # %B = full month name, %#d = day with no leading zero (Windows), %Y = year.
    day_label = leave_date.strftime("%B %#d, %Y")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless, slow_mo=slow_mo)
            context = browser.new_context()
            page = context.new_page()

            # 1. Log in.
            page.goto(LOGIN_URL)
            page.locator("#email").fill(username)
            page.locator("#password").fill(password)
            page.get_by_role("button", name="Login").click()

            # 2. Dismiss the onboarding tour if it shows up (not always present).
            try:
                page.get_by_role("button", name="Skip").click(timeout=5000)
            except Exception:
                pass

            # 3. Open the Apply Leave form.
            page.get_by_role("button", name="Leave").click()
            page.get_by_role("button", name="Apply Leave").first.click()

            # 4. Pick the leave type from the searchable react-select dropdown:
            #    open it, type the name to filter, press Enter to select.
            page.locator(".selectsearch").first.click()
            page.keyboard.type(leave_type)
            page.keyboard.press("Enter")

            # 5. Single day.
            page.get_by_role("radio", name="Single day").check()

            # 6. Open the date picker and click the matching day. The picker-open
            #    control is positional (validated); the explicit wait on the day
            #    button makes a wrong/closed calendar fail fast and clearly.
            page.get_by_role("button").nth(1).click()
            day_button = page.get_by_role("button", name=day_label)
            day_button.wait_for(state="visible", timeout=8000)
            day_button.click()

            # 7. Full day.
            page.get_by_role("radio", name="Full day").check()

            # 8. Reason.
            page.get_by_role("textbox").last.fill(reason)

            # 9. Submit (or stop here for a dry run).
            if submit:
                page.get_by_role("button", name="Apply Leave").click()
                page.wait_for_timeout(2500)
                result = {
                    "ok": True,
                    "message": f"Applied {leave_type} for {day_label}.",
                }
            else:
                result = {
                    "ok": True,
                    "message": (
                        f"Dry run OK — filled {leave_type} for {day_label}, "
                        "not submitted."
                    ),
                }

            browser.close()
            return result
    except Exception as exc:  # noqa: BLE001 -- report any failure to the caller
        return {"ok": False, "error": str(exc)}
