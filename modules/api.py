import base64
import json
import random
import time
import uuid
from urllib.parse import quote

import requests

BASE_URL = "https://discord.com/api/v9/"

COMMON_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-US,es-419;q=0.9",
    "X-Debug-Options": "bugReporterEnabled",
    "X-Discord-Locale": "en-US",
    "X-Discord-Timezone": "America/Buenos_Aires",
    "Referer": "https://discord.com/quest-home",
    "Origin": "https://discord.com",
}

CLIENT_VERSION = "1.0.9256"
CHROME_VERSION = "148.0.7778.280"
ELECTRON_VERSION = "42.9.0"
OS_VERSION = "10.0.19045"
OS_SDK_VERSION = "19045"
CLIENT_BUILD_NUMBER = 607562
NATIVE_BUILD_NUMBER = 89799

CLIENT_MOD_DETECTION_BITS = int(
    "0000000010000000000100000001000000001000000100000000100000000000001"
    "000001000000100000000010000000000001000000000000100000000000",
    2,
)


def discord_user_agent(client_version=CLIENT_VERSION):
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) discord/{client_version} Chrome/{CHROME_VERSION} "
        f"Electron/{ELECTRON_VERSION} Safari/537.36"
    )


class SuperProperties:
    def __init__(self):
        self.launch_signature = self._clean_launch_signature()
        self.client_launch_id = str(uuid.uuid4())
        self.heartbeat_session_id = str(uuid.uuid4())

    @staticmethod
    def _clean_launch_signature():
        value = uuid.uuid4().int & (~CLIENT_MOD_DETECTION_BITS & ((1 << 128) - 1))
        hex_value = f"{value:032x}"
        return (
            f"{hex_value[0:8]}-{hex_value[8:12]}-{hex_value[12:16]}-"
            f"{hex_value[16:20]}-{hex_value[20:32]}"
        )

    def as_dict(self):
        user_agent = discord_user_agent()
        return {
            "os": "Windows",
            "browser": "Discord Client",
            "release_channel": "stable",
            "client_version": CLIENT_VERSION,
            "os_version": OS_VERSION,
            "os_arch": "x64",
            "app_arch": "x64",
            "system_locale": "en-US",
            "has_client_mods": False,
            "browser_user_agent": user_agent,
            "browser_version": ELECTRON_VERSION,
            "os_sdk_version": OS_SDK_VERSION,
            "client_build_number": CLIENT_BUILD_NUMBER,
            "native_build_number": NATIVE_BUILD_NUMBER,
            "client_event_source": None,
            "launch_signature": self.launch_signature,
            "client_launch_id": self.client_launch_id,
            "client_heartbeat_session_id": self.heartbeat_session_id,
            "client_app_state": "focused",
        }

    def encoded(self):
        return base64.b64encode(
            json.dumps(self.as_dict(), separators=(",", ":")).encode("utf-8")
        ).decode("ascii")

    def user_agent(self):
        return discord_user_agent()

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
MAX_RETRIES = 3
REQUEST_TIMEOUT = 30
BACKOFF_DELAYS = [2, 4, 8]
TRACE_BODY_LIMIT = 20000
ERROR_BODY_LIMIT = 400


class ApiError(Exception):
    def __init__(self, status, body):
        snippet = (body or "")[:ERROR_BODY_LIMIT]
        super().__init__(f"discord returned HTTP {status}: {snippet}")
        self.status = status
        self.body = body or ""


class Client:
    def __init__(self, token, base_url=BASE_URL, trace=None):
        self.token = token
        self.base_url = base_url.rstrip("/") + "/"
        self.trace = trace
        self.super_properties = SuperProperties()
        self.session = requests.Session()
        self.session.headers.update(COMMON_HEADERS)
        self.session.headers["User-Agent"] = self.super_properties.user_agent()
        self.session.headers["X-Super-Properties"] = self.super_properties.encoded()

    def _url(self, path):
        return self.base_url + path

    def _trace(self, method, path, payload, status, body):
        if not self.trace:
            return
        entry = {
            "method": method,
            "endpoint": path,
            "authorization": "<redacted>",
            "body": payload,
            "status": status,
            "response": (body or "")[:TRACE_BODY_LIMIT],
        }
        self.trace(json.dumps(entry, ensure_ascii=False))

    def _retry_delay(self, response, attempt):
        reset_after = response.headers.get("X-RateLimit-Reset-After")
        if reset_after:
            try:
                return float(reset_after)
            except ValueError:
                pass
        if response.status_code == 429:
            try:
                data = response.json()
                if isinstance(data, dict) and "retry_after" in data:
                    return float(data["retry_after"])
            except (ValueError, json.JSONDecodeError):
                pass
        base = BACKOFF_DELAYS[min(attempt, len(BACKOFF_DELAYS) - 1)]
        return base + random.random() * 0.499

    def request(self, method, path, payload=None):
        url = self._url(path)
        headers = {"Authorization": self.token}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        attempt = 0
        while True:
            try:
                response = self.session.request(
                    method,
                    url,
                    json=payload,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException as exc:
                if attempt < MAX_RETRIES:
                    delay = BACKOFF_DELAYS[min(attempt, len(BACKOFF_DELAYS) - 1)]
                    time.sleep(delay + random.random() * 0.499)
                    attempt += 1
                    continue
                raise ApiError(0, str(exc))
            body = response.text
            self._trace(method, path, payload, response.status_code, body)
            if response.status_code in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                time.sleep(self._retry_delay(response, attempt))
                attempt += 1
                continue
            if 200 <= response.status_code < 300:
                if not body:
                    return None
                try:
                    return response.json()
                except ValueError:
                    raise ApiError(response.status_code, f"invalid JSON response: {body[:ERROR_BODY_LIMIT]}")
            raise ApiError(response.status_code, body)

    def get_me(self):
        return self.request("GET", "users/@me")

    def get_quests(self):
        return self.request("GET", "quests/@me")

    def get_channels(self):
        return self.request("GET", "users/@me/channels")

    def get_games(self, application_id):
        return self.request("GET", f"games?game_ids={quote(str(application_id), safe='')}")

    def get_applications_public(self, application_id):
        query = quote(str(application_id), safe="")
        return self.request("GET", f"applications/public?application_ids={query}")

    def enroll(self, quest_id, traffic_metadata_sealed):
        path = f"quests/{quote(str(quest_id), safe='')}/enroll"
        payload = {
            "location": 11,
            "is_targeted": False,
            "metadata_sealed": None,
            "traffic_metadata_sealed": traffic_metadata_sealed,
        }
        return self.request("POST", path, payload)

    def video_progress(self, quest_id, timestamp):
        path = f"quests/{quote(str(quest_id), safe='')}/video-progress"
        return self.request("POST", path, {"timestamp": timestamp})

    def heartbeat(self, quest_id, payload):
        path = f"quests/{quote(str(quest_id), safe='')}/heartbeat"
        return self.request("POST", path, payload)
