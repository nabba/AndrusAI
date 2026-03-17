"""
Forwards inbound Signal messages from signal-cli JSON-RPC to the FastAPI gateway.
Run this alongside signal-cli as a separate service.
"""
import socket
import json
import requests
import os

SOCKET_PATH = os.environ.get("SIGNAL_SOCKET_PATH", "/tmp/signal-cli.sock")
GATEWAY_URL = "http://127.0.0.1:8765/signal/inbound"


def listen():
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(SOCKET_PATH)
    buffer = b""

    while True:
        data = sock.recv(4096)
        if not data:
            break
        buffer += data

        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            try:
                msg = json.loads(line)
                # Only forward text DMs (SYNC_MESSAGE or RECEIPT excluded)
                if msg.get("params", {}).get("dataMessage"):
                    dm = msg["params"]
                    payload = {
                        "sender": dm.get("source"),
                        "message": dm["dataMessage"].get("message", ""),
                    }
                    requests.post(GATEWAY_URL, json=payload, timeout=5)
            except Exception:
                pass


if __name__ == "__main__":
    listen()
