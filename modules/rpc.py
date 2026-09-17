import json
import os
import socket
import struct
import sys
import uuid


class RpcError(Exception):
    pass


class DiscordRpc:
    def __init__(self, client_id):
        self.client_id = str(client_id)
        self.connection = None
        self._connect()
        self._send(0, {"v": 1, "client_id": self.client_id})
        _, ready = self._receive()
        if ready.get("evt") != "READY":
            self.close()
            raise RpcError(f"Discord RPC handshake failed: {ready}")

    def _connect(self):
        if sys.platform == "win32":
            for index in range(10):
                try:
                    self.connection = open(rf"\\?\pipe\discord-ipc-{index}", "r+b", buffering=0)
                    return
                except OSError:
                    continue
        else:
            for index in range(10):
                path = os.path.join(
                    os.environ.get("XDG_RUNTIME_DIR")
                    or os.environ.get("TMPDIR")
                    or os.environ.get("TMP")
                    or os.environ.get("TEMP")
                    or "/tmp",
                    f"discord-ipc-{index}",
                )
                try:
                    self.connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.connection.connect(path)
                    return
                except OSError:
                    if self.connection:
                        self.connection.close()
                    self.connection = None
        raise RpcError("Discord desktop IPC is not available")

    def _write(self, data):
        if isinstance(self.connection, socket.socket):
            self.connection.sendall(data)
        else:
            self.connection.write(data)
            self.connection.flush()

    def _read(self, size):
        chunks = []
        remaining = size
        while remaining:
            chunk = self.connection.recv(remaining) if isinstance(self.connection, socket.socket) else self.connection.read(remaining)
            if not chunk:
                raise RpcError("Discord RPC connection closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _send(self, opcode, payload):
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._write(struct.pack("<II", opcode, len(encoded)) + encoded)

    def _receive(self):
        opcode, length = struct.unpack("<II", self._read(8))
        payload = json.loads(self._read(length).decode("utf-8"))
        return opcode, payload

    def set_activity(self, name, start_timestamp):
        nonce = str(uuid.uuid4())
        self._send(
            1,
            {
                "cmd": "SET_ACTIVITY",
                "args": {
                    "pid": os.getpid(),
                    "activity": {
                        "name": str(name)[:128],
                        "type": 0,
                        "details": "Playing",
                        "timestamps": {"start": int(start_timestamp * 1000)},
                        "instance": True,
                    },
                },
                "nonce": nonce,
            },
        )
        _, response = self._receive()
        if response.get("evt") == "ERROR" or response.get("data", {}).get("error"):
            raise RpcError(str(response))
        return response

    def clear_activity(self):
        nonce = str(uuid.uuid4())
        self._send(
            1,
            {"cmd": "SET_ACTIVITY", "args": {"pid": os.getpid(), "activity": None}, "nonce": nonce},
        )
        self._receive()

    def close(self):
        if self.connection is not None:
            try:
                self.connection.close()
            finally:
                self.connection = None
