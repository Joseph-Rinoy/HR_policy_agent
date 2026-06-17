"""Manual dry-run check for the refactored leave_automation module.

Runs apply_leave() in a VISIBLE browser and STOPS before submitting, so you can
confirm the selectors still work end-to-end without filing a real leave.

    python test_leave_dryrun.py
"""

from datetime import date
from getpass import getpass

from leave_automation import LEAVE_TYPES, apply_leave

email = input("sumHR email: ").strip()
password = getpass("sumHR password (hidden): ")

print("\nLeave types:", ", ".join(LEAVE_TYPES))
leave_type = input("Leave type (exact): ").strip() or "Sick Leave"
day = int(input("Day of THIS month to apply for (e.g. 12): ").strip())

leave_date = date.today().replace(day=day)

result = apply_leave(
    (email, password),
    leave_type,
    leave_date,
    "automation dry run",
    submit=False,       # <-- does NOT click the final Apply
    headless=False,     # <-- visible so you can watch
    slow_mo=500,
)
print("\nRESULT:", result)
