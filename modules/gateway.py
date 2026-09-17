import json
import threading
import time
import uuid

import websocket


GATEWAY_URL = "wss://gateway.discord.gg/?v=9&encoding=json"


class GatewayError(Exception):
    pass


class GatewayPresence:
    def __init__(self, token, properties=None):
        self.token = token
        self.properties = properties or {
            "os": "Windows",
            "browser": "Discord Client",
            "device": "",
            "system_locale": "en-US",
        }
        self.activity = None
        self.socket = None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self.thread = None
        self.sequence = None
        self.heartbeat_interval = 41250 / 1000

    def start(self, name, application_id):
        self.activity = self._activity(name, application_id)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _activity(self, name, application_id):
        return {
            "name": str(name)[:128],
            "type": 0,
            "application_id": str(application_id),
            "timestamps": {"start": int(time.time() * 1000)},
        }

    def set_activity(self, name, application_id):
        activity = self._activity(name, application_id)
        self.activity = activity
        if self.ready_event.is_set():
            try:
                self._send_presence()
            except (OSError, websocket.WebSocketException):
                self.ready_event.clear()

    def _send(self, payload):
        with self.lock:
            if self.socket is not None:
                self.socket.send(json.dumps(payload, separators=(",", ":")))

    def _send_presence(self):
        self._send(
            {
                "op": 3,
                "d": {
                    "since": 0,
                    "activities": [self.activity] if self.activity else [],
                    "status": "online",
                    "afk": False,
                },
            }
        )

    def _run(self):
        retry_delay = 1.0
        while not self.stop_event.is_set():
            try:
                self.socket = websocket.create_connection(GATEWAY_URL, timeout=2)
                self.socket.settimeout(1)
                hello = json.loads(self.socket.recv())
                if hello.get("op") != 10:
                    raise GatewayError(f"unexpected gateway hello: {hello}")
                self.heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000
                self._send(
                    {
                        "op": 2,
                        "d": {
                            "token": self.token,
                            "capabilities": 30717,
                            "properties": self.properties,
                            "presence": {
                                "since": 0,
                                "activities": [self.activity] if self.activity else [],
                                "status": "online",
                                "afk": False,
                            },
                            "compress": False,
                        },
                    }
                )
                self.ready_event.set()
                retry_delay = 1.0
                next_heartbeat = time.monotonic() + self.heartbeat_interval
                while not self.stop_event.is_set():
                    now = time.monotonic()
                    if now >= next_heartbeat:
                        self._send({"op": 1, "d": self.sequence})
                        next_heartbeat = now + self.heartbeat_interval
                    try:
                        message = json.loads(self.socket.recv())
                        if "s" in message and message["s"] is not None:
                            self.sequence = message["s"]
                        if message.get("op") in {7, 9}:
                            raise GatewayError("Discord requested a Gateway reconnect")
                        if message.get("op") == 1:
                            self._send({"op": 1, "d": self.sequence})
                    except websocket.WebSocketTimeoutException:
                        continue
            except (OSError, websocket.WebSocketException, GatewayError, KeyError, ValueError):
                self.ready_event.clear()
            finally:
                if self.socket is not None:
                    try:
                        self.socket.close()
                    except OSError:
                        pass
                self.socket = None
            if not self.stop_event.is_set():
                self.stop_event.wait(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)

    def close(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self.ready_event.clear()
