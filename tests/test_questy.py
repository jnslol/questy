"""Offline tests for Questy using a mock client (no network)."""

import sys
import threading
import unittest
import copy
from unittest import mock

sys.path.insert(0, ".")

from modules.api import ApiError, Client  # noqa: E402
from modules.quest import Quest, parse_quests_response  # noqa: E402
from modules.rpc import DiscordRpc  # noqa: E402
from modules.gateway import GatewayPresence  # noqa: E402
from modules.runner import Cancelled, Runner  # noqa: E402


QUEST_ENVELOPE = {
    "quests": [
        {
            "id": "q-video",
            "config": {
                "expires_at": "2099-01-01T00:00:00Z",
                "task_config_v2": {
                    "tasks": {"WATCH_VIDEO": {"target": 6, "applications": []}}
                },
                "messages": {"quest_name": "Video Quest"},
            },
            "user_status": {"enrolled_at": "2026-01-01T00:00:00Z", "progress": {}},
        },
        {
            "id": "q-play",
            "config": {
                "task_config": {
                    "tasks": {"PLAY_ON_DESKTOP": {"target": 40}}
                },
                "messages": {"quest_name": "Play Quest"},
                "application": {"id": "app-1", "name": "Game"},
            },
            "user_status": {},
            "traffic_metadata_sealed": "sealed-1",
        },
        {
            "id": "q-done",
            "config": {
                "task_config_v2": {"tasks": {"WATCH_VIDEO": {"target": 60}}},
                "messages": {"quest_name": "Done Quest"},
            },
            "user_status": {"completed_at": "2026-01-01T00:00:00Z"},
        },
    ],
    "excluded_quests": [],
    "quest_enrollment_blocked_until": None,
    "quest_access_suspended_until": None,
}


class MockClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []
        self.play_credited = False

    def get_quests(self):
        envelope = copy.deepcopy(QUEST_ENVELOPE)
        if self.play_credited:
            envelope["quests"][1]["user_status"] = {
                "completed_at": "2026-01-01T00:00:00Z",
                "progress": {"PLAY_ON_DESKTOP": {"value": 40}},
            }
        return envelope

    def enroll(self, quest_id, sealed):
        self.calls.append(("enroll", quest_id, sealed))

    def video_progress(self, quest_id, timestamp):
        self.calls.append(("video", quest_id, timestamp))
        override = self.responses.get(("video", quest_id))
        if override:
            return override
        return {
            "completed_at": "2026-01-01T00:00:00Z" if timestamp >= 6 else None,
            "progress": {"WATCH_VIDEO": {"value": timestamp}},
        }

    def heartbeat(self, quest_id, payload):
        self.calls.append(("heartbeat", quest_id, dict(payload)))
        if payload.get("terminal"):
            return {}
        if quest_id == "q-play":
            self.play_credited = True
        return {
            "completed_at": None,
            "progress": {"PLAY_ON_DESKTOP": {"value": 60}},
            "stream_progress_seconds": 60,
        }

    def get_channels(self):
        return [{"id": "ch-1"}]

    def get_me(self):
        return {"id": "user-1"}

    def get_games(self, app_id):
        return [
            {
                "id": app_id,
                "executables": [
                    {"name": "Launcher.exe", "os": "win32", "is_launcher": True},
                    {"name": "Game.exe", "os": "win32", "is_launcher": False},
                ],
            }
        ]

    def get_applications_public(self, app_id):
        return [
            {
                "id": app_id,
                "executables": [
                    {"name": "Launcher.exe", "os": "win32", "is_launcher": True},
                    {"name": "Game.exe", "os": "win32", "is_launcher": False},
                ],
            }
        ]


class TestQuestParsing(unittest.TestCase):
    def test_parse_envelope(self):
        quests, excluded, blocked, suspended = parse_quests_response(QUEST_ENVELOPE)
        self.assertEqual(len(quests), 3)
        self.assertEqual(excluded, [])
        self.assertIsNone(blocked)
        self.assertIsNone(suspended)

    def test_task_type_and_target(self):
        quests, _, _, _ = parse_quests_response(QUEST_ENVELOPE)
        self.assertEqual(quests[0].task_type(), "WATCH_VIDEO")
        self.assertEqual(quests[0].target(), 6)
        self.assertEqual(quests[1].task_type(), "PLAY_ON_DESKTOP")

    def test_task_application_id_overrides_config_application(self):
        quest = Quest(
            {
                "id": "x",
                "config": {
                    "application": {"id": "legacy-app"},
                    "task_config_v2": {
                        "tasks": {
                            "PLAY_ON_DESKTOP": {
                                "target": 60,
                                "applications": [{"id": "task-app"}],
                            }
                        }
                    },
                },
                "user_status": {},
            }
        )
        self.assertEqual(quest.application_id_for_task(), "task-app")

    def test_progress_does_not_mean_completed(self):
        quest = Quest(
            {
                "id": "x",
                "config": {
                    "task_config_v2": {
                        "tasks": {"PLAY_ON_DESKTOP": {"target": 900}}
                    }
                },
                "user_status": {
                    "completed_at": None,
                    "progress": {
                        "PLAY_ON_DESKTOP": {
                            "value": 176,
                            "updated_at": "2026-09-16T20:56:24.283673+00:00",
                        }
                    },
                },
            }
        )
        self.assertEqual(quest.local_progress(), 176)
        self.assertFalse(quest.is_completed())
        self.assertEqual(quest.progress_updated_at(), "2026-09-16T20:56:24.283673+00:00")

    def test_actionable(self):
        quests, _, _, _ = parse_quests_response(QUEST_ENVELOPE)
        self.assertTrue(quests[0].is_actionable())
        self.assertTrue(quests[1].is_actionable())
        self.assertFalse(quests[2].is_actionable())

    def test_camel_case_compat(self):
        quest = Quest(
            {
                "id": "x",
                "config": {
                    "taskConfigV2": {"tasks": {"PLAY_ACTIVITY": {"target": 30}}},
                    "questName": "Camel",
                },
                "user_status": {"completedAt": None, "streamProgressSeconds": 5},
            }
        )
        self.assertEqual(quest.task_type(), "PLAY_ACTIVITY")
        self.assertEqual(quest.name, "Camel")
        self.assertEqual(quest.local_progress(), 5)


class TestQuestFilters(unittest.TestCase):
    def make_row(self, task, status, name="q"):
        return ("main", name, task, "0/60", status)

    def test_no_filters_shows_all(self):
        from modules.tui import filter_quest_rows

        rows = [self.make_row("WATCH_VIDEO", "actionable"), self.make_row("PLAY_ON_DESKTOP", "expired")]
        self.assertEqual(filter_quest_rows(rows, status="all"), rows)

    def test_default_filter_shows_actionable(self):
        from modules.tui import filter_quest_rows

        rows = [self.make_row("WATCH_VIDEO", "actionable"), self.make_row("PLAY_ON_DESKTOP", "expired")]
        self.assertEqual(filter_quest_rows(rows), [rows[0]])

    def test_status_filter(self):
        from modules.tui import filter_quest_rows

        rows = [
            self.make_row("WATCH_VIDEO", "actionable", "a"),
            self.make_row("PLAY_ON_DESKTOP", "completed", "b"),
            self.make_row("PLAY_ON_DESKTOP", "expired", "c"),
        ]
        shown = filter_quest_rows(rows, status="actionable")
        self.assertEqual([r[1] for r in shown], ["a"])

    def test_type_filter(self):
        from modules.tui import filter_quest_rows

        rows = [
            self.make_row("WATCH_VIDEO", "actionable", "a"),
            self.make_row("PLAY_ON_DESKTOP", "actionable", "b"),
            self.make_row("STREAM_ON_DESKTOP", "actionable", "c"),
        ]
        shown = filter_quest_rows(rows, types={"PLAY_ON_DESKTOP"})
        self.assertEqual([r[1] for r in shown], ["b"])

    def test_combined_filters(self):
        from modules.tui import filter_quest_rows

        rows = [
            self.make_row("WATCH_VIDEO", "actionable", "a"),
            self.make_row("PLAY_ON_DESKTOP", "actionable", "b"),
            self.make_row("PLAY_ON_DESKTOP", "completed", "c"),
        ]
        shown = filter_quest_rows(rows, status="actionable", types={"WATCH_VIDEO", "PLAY_ON_DESKTOP"})
        self.assertEqual({r[1] for r in shown}, {"a", "b"})

    def test_empty_type_set_means_all(self):
        from modules.tui import filter_quest_rows

        rows = [self.make_row("PLAY_ACTIVITY", "actionable")]
        self.assertEqual(filter_quest_rows(rows, types=()), rows)

    def test_cli_quest_matches(self):
        from modules.cli import quest_matches
        from modules.quest import Quest

        quest = Quest(
            {
                "id": "x",
                "config": {
                    "task_config_v2": {"tasks": {"PLAY_ON_DESKTOP": {"target": 60}}},
                    "messages": {"quest_name": "T"},
                },
                "user_status": {"enrolled_at": "2026-01-01T00:00:00Z"},
            }
        )
        self.assertTrue(quest_matches(quest, "all", set()))
        self.assertTrue(quest_matches(quest, "actionable", {"PLAY_ON_DESKTOP"}))
        self.assertFalse(quest_matches(quest, "completed", set()))
        self.assertFalse(quest_matches(quest, "all", {"WATCH_VIDEO"}))


class TestRunner(unittest.TestCase):
    def setUp(self):
        self._patches = [
            mock.patch("modules.runner.HEARTBEAT_INTERVALS", {
                "PLAY_ON_DESKTOP": (0.01, 0.01),
                "STREAM_ON_DESKTOP": (0.01, 0.01),
                "PLAY_ACTIVITY": (0.01, 0.01),
            }),
            mock.patch("modules.runner.DEFAULT_HEARTBEAT_INTERVAL", (0.01, 0.01)),
            mock.patch("modules.runner.VIDEO_SLEEP", (0.05, 0.05)),
            mock.patch("modules.runner.POST_ENROLL_SLEEP", (0.0, 0.0)),
            mock.patch("modules.runner.QUEST_STAGGER", (0.0, 0.0)),
        ]
        for patch in self._patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_video_quest_completes(self):
        client = MockClient()
        runner = Runner(client, "test", lambda m: None)
        errors = runner.run()
        self.assertEqual(errors, [])
        self.assertEqual(runner.results["q-video"], "completed")
        video_calls = [c for c in client.calls if c[0] == "video"]
        self.assertEqual(video_calls[-1][2], 6)

    def test_runner_emits_live_progress_events(self):
        client = MockClient()
        events = []
        runner = Runner(
            client,
            "test",
            lambda m: None,
            event=lambda **event: events.append(event),
        )
        runner.run()
        video_events = [event for event in events if event["quest_id"] == "q-video"]
        self.assertTrue(any(event["status"] == "running" for event in video_events))
        self.assertTrue(any(event["progress"] == 6 for event in video_events))
        self.assertTrue(any(event["status"] == "completed" for event in video_events))

    def test_completed_quest_skipped(self):
        client = MockClient()
        runner = Runner(client, "test", lambda m: None)
        runner.run()
        self.assertEqual(runner.results["q-done"], "skipped")

    def test_heartbeat_terminal_sent(self):
        client = MockClient()
        runner = Runner(client, "test", lambda m: None)
        runner.run()
        heartbeats = [c for c in client.calls if c[0] == "heartbeat" and c[1] == "q-play"]
        self.assertTrue(heartbeats[-1][2]["terminal"] is True)
        self.assertEqual(heartbeats[-1][2]["application_id"], "app-1")
        self.assertEqual(heartbeats[-1][2]["executable_path"], "Game.exe")

    def test_enroll_called_with_sealed_metadata(self):
        client = MockClient()
        runner = Runner(client, "test", lambda m: None)
        runner.run()
        enrolls = [c for c in client.calls if c[0] == "enroll" and c[1] == "q-play"]
        self.assertEqual(len(enrolls), 1)
        self.assertEqual(enrolls[0][2], "sealed-1")

    def test_error_quest_does_not_stop_siblings(self):
        client = MockClient()
        client.video_progress = mock.Mock(
            side_effect=ApiError(500, "server error"), __name__="video_progress"
        )
        runner = Runner(client, "test", lambda m: None)
        errors = runner.run()
        self.assertEqual(runner.results["q-play"], "completed")
        self.assertIn("q-video", errors)

    def test_403_enroll_skips_quest(self):
        client = MockClient()
        client.enroll = mock.Mock(
            side_effect=ApiError(403, "not entitled"), __name__="enroll"
        )
        runner = Runner(client, "test", lambda m: None)
        errors = runner.run()
        self.assertEqual(runner.results["q-play"], "skipped (unavailable)")
        self.assertNotIn("q-play", errors)

    def test_no_progress_aborts_heartbeat(self):
        client = MockClient()
        client.heartbeat = mock.Mock(
            side_effect=lambda qid, payload: {"completed_at": None, "progress": {}, "stream_progress_seconds": 0}
            if not payload.get("terminal")
            else {},
            __name__="heartbeat",
        )
        runner = Runner(client, "test", lambda m: None, desktop_idle_limit=1)
        errors = runner.run()
        self.assertEqual(runner.results["q-play"], "active")

    def test_target_progress_without_server_completion_stays_active(self):
        client = MockClient()
        client.heartbeat = mock.Mock(
            side_effect=lambda qid, payload: {
                "completed_at": None,
                "progress": {"PLAY_ON_DESKTOP": {"value": 60}},
            }
            if not payload.get("terminal")
            else {},
            __name__="heartbeat",
        )
        runner = Runner(client, "test", lambda m: None)
        runner.run()
        self.assertEqual(runner.results["q-play"], "active")

    def test_suspension_stops_runner(self):
        client = MockClient()
        client.get_quests = lambda: {
            **QUEST_ENVELOPE,
            "quest_access_suspended_until": "2099-01-01T00:00:00Z",
        }
        logs = []
        runner = Runner(client, "test", logs.append)
        errors = runner.run()
        self.assertTrue(errors)
        self.assertTrue(any("suspended" in e for e in errors))

    def test_cancellation(self):
        client = MockClient()
        cancel = threading.Event()
        cancel.set()
        runner = Runner(client, "test", lambda m: None, cancel_event=cancel)
        with self.assertRaises(Cancelled):
            runner.run()


class TestSuperProperties(unittest.TestCase):
    def test_encoded_decodes(self):
        import base64
        import json as jsonlib

        client = Client("token")
        decoded = jsonlib.loads(base64.b64decode(client.session.headers["X-Super-Properties"]))
        self.assertFalse(decoded["has_client_mods"])
        self.assertIn("launch_signature", decoded)
        self.assertIn("client_launch_id", decoded)
        self.assertIn("client_heartbeat_session_id", decoded)
        self.assertEqual(decoded["client_build_number"], 607562)

    def test_user_agent_matches_xsp(self):
        import base64
        import json as jsonlib

        client = Client("token")
        decoded = jsonlib.loads(base64.b64decode(client.session.headers["X-Super-Properties"]))
        self.assertEqual(
            client.session.headers["User-Agent"], decoded["browser_user_agent"]
        )

    def test_session_ids_stable_per_client(self):
        client = Client("token")
        first = client.super_properties.encoded()
        second = client.super_properties.encoded()
        self.assertEqual(first, second)
        other = Client("token")
        self.assertNotEqual(client.super_properties.client_launch_id, other.super_properties.client_launch_id)

    def test_launch_signature_clears_detection_bits(self):
        import uuid as uuidlib

        from modules.api import CLIENT_MOD_DETECTION_BITS

        signature = uuidlib.UUID(Client("token").super_properties.launch_signature)
        self.assertEqual(signature.int & CLIENT_MOD_DETECTION_BITS, 0)


class TestDiscordRpc(unittest.TestCase):
    def test_set_activity_payload(self):
        rpc = object.__new__(DiscordRpc)
        rpc._send = mock.Mock()
        rpc._receive = mock.Mock(return_value=(1, {"cmd": "SET_ACTIVITY", "evt": None}))
        rpc.set_activity("Example Game", 123.0)
        opcode, payload = rpc._send.call_args.args
        self.assertEqual(opcode, 1)
        self.assertEqual(payload["cmd"], "SET_ACTIVITY")
        self.assertEqual(payload["args"]["activity"]["name"], "Example Game")
        self.assertEqual(payload["args"]["activity"]["type"], 0)
        self.assertEqual(payload["args"]["activity"]["timestamps"]["start"], 123000)

    def test_clear_activity_payload(self):
        rpc = object.__new__(DiscordRpc)
        rpc._send = mock.Mock()
        rpc._receive = mock.Mock(return_value=(1, {"cmd": "SET_ACTIVITY", "evt": None}))
        rpc.clear_activity()
        _, payload = rpc._send.call_args.args
        self.assertIsNone(payload["args"]["activity"])


class TestGatewayPresence(unittest.TestCase):
    def test_activity_payload_is_online_game_activity(self):
        gateway = GatewayPresence("token")
        activity = gateway._activity("Example Game", "app-1")
        self.assertEqual(activity["name"], "Example Game")
        self.assertEqual(activity["type"], 0)
        self.assertEqual(activity["application_id"], "app-1")
        self.assertIn("start", activity["timestamps"])


class TestApiRetry(unittest.TestCase):
    def _response(self, status, headers=None, body="", json_data=None):
        response = mock.Mock()
        response.status_code = status
        response.headers = headers or {}
        response.text = body
        if json_data is not None:
            response.json = mock.Mock(return_value=json_data)
        else:
            response.json = mock.Mock(side_effect=ValueError)
        return response

    def test_retry_on_429_then_success(self):
        client = Client("token")
        responses = [
            self._response(429, {"X-RateLimit-Reset-After": "0"}),
            self._response(429, {"X-RateLimit-Reset-After": "0"}),
            self._response(200, body='{"id": "u"}', json_data={"id": "u"}),
        ]
        with mock.patch.object(client.session, "request", side_effect=responses), \
                mock.patch("time.sleep"):
            result = client.get_me()
        self.assertEqual(result, {"id": "u"})

    def test_gives_up_after_retries(self):
        client = Client("token")
        responses = [self._response(500, body="err")] * 10
        with mock.patch.object(client.session, "request", side_effect=responses), \
                mock.patch("time.sleep"):
            with self.assertRaises(ApiError):
                client.get_me()

    def test_non_retryable_raises_immediately(self):
        client = Client("token")
        request = mock.Mock(return_value=self._response(401, body="unauthorized"))
        with mock.patch.object(client.session, "request", request):
            with self.assertRaises(ApiError) as ctx:
                client.get_me()
        self.assertIn("HTTP 401", str(ctx.exception))

    def test_error_message_truncated(self):
        client = Client("token")
        request = mock.Mock(return_value=self._response(400, body="x" * 1000))
        with mock.patch.object(client.session, "request", request):
            with self.assertRaises(ApiError) as ctx:
                client.get_me()
        self.assertLessEqual(len(str(ctx.exception)), 460)

    def test_url_encoding(self):
        client = Client("token")
        request = mock.Mock(return_value=self._response(200, body="[]", json_data=[]))
        with mock.patch.object(client.session, "request", request):
            client.get_games("app/id 1")
        self.assertIn("games?game_ids=app%2Fid%201", request.call_args[0][1])


if __name__ == "__main__":
    unittest.main()
