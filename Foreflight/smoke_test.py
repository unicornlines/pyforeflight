"""
Read-only smoke test for the Foreflight web client.

Logs in with FF_USERNAME / FF_PASSWORD from the environment and exercises the
read endpoints, printing a short summary. Performs no writes or deletions.

    FF_USERNAME=... FF_PASSWORD=... python -m Foreflight.smoke_test
"""

import logging
import os

from .client import Client
from .exceptions import ForeflightException


def _count(value) -> str:
    try:
        return str(len(value))
    except TypeError:
        return str(value)


def run(username: str, password: str) -> int:
    client = Client()
    client.login(username, password)

    checks = [
        ("config keys", lambda: list(client.get_config().keys())),
        ("aircraft", client.get_aircraft),
        ("roles", client.get_roles),
        ("custom fields", client.get_custom_field_infos),
        ("drafts count", client.get_drafts_count),
        ("lb4l status", client.get_lb4l_status),
        ("report types", client.get_report_types),
        ("entries (total)", lambda: client.get_entries(size=1).get("totalElements")),
        ("all entries", client.get_all_entries),
        ("all persons", client.get_all_persons),
        ("airport KAUS city", lambda: client.get_airport("KAUS").get("city")),
        ("procedures KAUS", lambda: client.get_procedures("KAUS")),
    ]

    failures = 0
    for label, fn in checks:
        try:
            result = fn()
            print(f"  OK   {label:22} -> {_count(result)}")
        except ForeflightException as exc:
            failures += 1
            print(f"  FAIL {label:22} -> {exc}")

    client.logout()
    print(f"\n{len(checks) - failures}/{len(checks)} checks passed.")
    return 1 if failures else 0


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    username = os.environ.get("FF_USERNAME")
    password = os.environ.get("FF_PASSWORD")
    if not username or not password:
        raise SystemExit("Set FF_USERNAME and FF_PASSWORD in the environment.")
    return run(username, password)


if __name__ == "__main__":
    raise SystemExit(main())
