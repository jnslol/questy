import json

import requests


def format_run_summary(results):
    lines = ["Questy run completed", ""]
    total_errors = 0
    total_completed = 0
    total_active = 0

    for label, result in results.items():
        statuses = result.get("statuses", {})
        completed = sum(1 for status in statuses.values() if status == "completed")
        active = sum(1 for status in statuses.values() if status == "active")
        errors = result.get("errors", [])
        total_completed += completed
        total_active += active
        total_errors += len(errors)
        lines.append(f"{label}: {completed} completed, {active} active, {len(errors)} errors")
        for error in errors[:3]:
            lines.append(f"  - {error}")

    lines.extend(
        [
            "",
            f"Total: {total_completed} completed, {total_active} active, {total_errors} errors",
        ]
    )
    return "\n".join(lines)


def send_webhook(webhook_url, results, timeout=15):
    if not webhook_url:
        return False
    response = requests.post(
        webhook_url,
        json={"content": format_run_summary(results), "results": results},
        timeout=timeout,
    )
    response.raise_for_status()
    return True


def safe_json(value):
    return json.loads(json.dumps(value, default=str))
