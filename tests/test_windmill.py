import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, ".")

from modules.accounts import parse_accounts  # noqa: E402
from modules.cli import run_all_accounts  # noqa: E402
from modules.notifications import format_run_summary, send_webhook  # noqa: E402


class TestWindmillSupport(unittest.TestCase):
    def test_parse_accounts_accepts_secret_json_shapes(self):
        self.assertEqual(
            parse_accounts(json.loads('[{"label": "Main", "token": "secret"}]')),
            [{"label": "Main", "token": "secret"}],
        )
        self.assertEqual(
            parse_accounts({"Main": "secret"}),
            [{"label": "Main", "token": "secret"}],
        )

    def test_summary_contains_account_results(self):
        summary = format_run_summary(
            {
                "Main": {
                    "statuses": {"q1": "completed", "q2": "active"},
                    "errors": [],
                }
            }
        )
        self.assertIn("Main: 1 completed, 1 active, 0 errors", summary)

    def test_generated_script_has_flow_accounts_and_no_runtime_imports(self):
        source = Path("f/questy/run_all_accounts.py").read_text(encoding="utf-8")
        self.assertIn("accounts: list[dict]", source)
        self.assertNotIn("from _questy_runtime", source)

    @mock.patch("modules.cli.run_account")
    def test_run_all_accounts_collects_each_account_result(self, run_account):
        class FakeRunner:
            results = {"quest": "completed"}

        def run_fake(client, label, *args, **kwargs):
            return FakeRunner(), [] if label == "Main" else ["error: unavailable"]

        run_account.side_effect = run_fake
        results = run_all_accounts(
            [
                {"label": "Main", "token": "token-1"},
                {"label": "Secondary", "token": "token-2"},
            ],
            log=lambda message: None,
        )
        self.assertEqual(results["Main"]["statuses"], {"quest": "completed"})
        self.assertEqual(results["Secondary"]["errors"], ["error: unavailable"])

    @mock.patch("modules.cli.run_account")
    def test_run_all_accounts_can_select_one_account(self, run_account):
        class FakeRunner:
            results = {}

        run_account.return_value = (FakeRunner(), [])
        results = run_all_accounts(
            [
                {"label": "Main", "token": "token-1"},
                {"label": "Secondary", "token": "token-2"},
            ],
            account="Secondary",
            log=lambda message: None,
        )
        self.assertEqual(list(results), ["Secondary"])
        self.assertEqual(run_account.call_args.args[1], "Secondary")

    @mock.patch("modules.notifications.requests.post")
    def test_webhook_payload_contains_summary(self, post):
        post.return_value.raise_for_status.return_value = None
        self.assertTrue(
            send_webhook(
                "https://example.test/hook",
                {"Main": {"statuses": {}, "errors": []}},
            )
        )
        payload = post.call_args.kwargs["json"]
        self.assertIn("Questy run completed", payload["content"])


if __name__ == "__main__":
    unittest.main()
