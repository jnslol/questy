"""Windmill entrypoint for running all configured Questy accounts."""

import json

from .runtime.accounts import parse_accounts
from .runtime.cli_service import run_all_accounts
from .runtime.notifications import safe_json, send_webhook


def _get_variable(path):
    import wmill

    return wmill.get_variable(path)


def _load_accounts():
    value = _get_variable("f/questy/accounts")
    if isinstance(value, str):
        value = json.loads(value)
    accounts = parse_accounts(value)
    if not accounts:
        raise ValueError("f/questy/accounts does not contain any accounts")
    return accounts


def main(enable_rpc: bool = True, enable_gateway: bool = True):
    results = {}
    run_error = None
    notification_sent = False
    notification_error = None

    try:
        results = run_all_accounts(
            _load_accounts(),
            log=print,
            auto_enroll=True,
            enable_rpc=enable_rpc,
            enable_gateway=enable_gateway,
        )
    except Exception as exc:
        run_error = str(exc)
        print(f"Questy run failed: {run_error}")
    finally:
        try:
            webhook_url = _get_variable("f/questy/notification_webhook")
            if not webhook_url:
                raise ValueError("f/questy/notification_webhook is empty")
            notification_sent = send_webhook(
                webhook_url,
                results
                or {
                    "_run": {
                        "errors": [run_error or "run failed"],
                        "statuses": {},
                    }
                },
            )
        except Exception as exc:
            notification_error = f"notification failed: {exc}"
            print(notification_error)

    return {
        "success": (
            not run_error
            and not notification_error
            and not any(item["errors"] for item in results.values())
        ),
        "notification_sent": notification_sent,
        "notification_error": notification_error,
        "results": safe_json(results),
        "run_error": run_error,
    }
