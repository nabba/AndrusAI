"""
Forwards inbound Signal messages from signal-cli JSON-RPC to the FastAPI gateway.

signal-cli must be running in daemon mode with --receive-mode manual:
    signal-cli -a +NUMBER daemon --http 127.0.0.1:7583 --receive-mode manual

Environment variables:
    GATEWAY_SECRET        — shared secret for authenticating with the gateway
    SIGNAL_CLI_HTTP_URL   — signal-cli HTTP endpoint (default: http://127.0.0.1:7583)
    GATEWAY_URL           — gateway inbound endpoint (default: http://127.0.0.1:8765/signal/inbound)
"""
import json
import os
import sys
import time
import requests

SIGNAL_CLI_URL = os.environ.get("SIGNAL_CLI_HTTP_URL", "http://127.0.0.1:7583")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8765/signal/inbound")
GATEWAY_SECRET = os.environ.get("GATEWAY_SECRET", "")

_signal_session = requests.Session()
_signal_session.headers["Content-Type"] = "application/json"
_gateway_session = requests.Session()


def log(msg):
    print(f"[forwarder] {msg}", flush=True)


def _receive_messages():
    """Single receive call with short timeout.

    Returns: list of messages, or None on connection error (distinct from empty []).
    """
    try:
        resp = _signal_session.post(
            SIGNAL_CLI_URL.rstrip("/") + "/api/v1/rpc",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "receive",
                "params": {"timeout": 1},
            },
            timeout=10,
        )
        data = resp.json()
        if "error" in data:
            err = data["error"].get("message", "")
            if "already being received" in err:
                return []
            log(f"receive error: {err}")
            return []
        return data.get("result", [])
    except requests.exceptions.ConnectionError:
        return None  # Signal connection error — distinct from empty
    except requests.exceptions.ReadTimeout:
        return []
    except Exception as e:
        log(f"receive failed: {e}")
        return None


def _check_signal_cli_alive() -> bool:
    """Quick health check on signal-cli."""
    try:
        resp = _signal_session.post(
            SIGNAL_CLI_URL.rstrip("/") + "/api/v1/rpc",
            json={"jsonrpc": "2.0", "id": 1, "method": "version"},
            timeout=3,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _wait_for_signal_cli():
    """Block until signal-cli responds (reuses startup logic)."""
    log("Waiting for signal-cli to come back...")
    while True:
        if _check_signal_cli_alive():
            log("signal-cli reconnected")
            return
        time.sleep(5)


def _process_envelope(envelope: dict) -> None:
    """Extract message data from an envelope and forward to the gateway."""
    data_msg = envelope.get("dataMessage")
    if not data_msg:
        return

    # Handle emoji reactions (feedback signals)
    reaction = data_msg.get("reaction")
    if reaction:
        sender = envelope.get("source") or envelope.get("sourceNumber")
        if not sender:
            return
        emoji = reaction.get("emoji", "")
        target_ts = reaction.get("targetSentTimestamp", 0)
        is_remove = reaction.get("isRemove", False)
        log(f"Reaction from {sender[-4:]}: {emoji} on ts={target_ts} (remove={is_remove})")

        payload = {
            "type": "reaction_feedback",
            "sender": sender,
            "emoji": emoji,
            "target_timestamp": target_ts,
            "is_remove": is_remove,
        }
        headers = {}
        if GATEWAY_SECRET:
            headers["Authorization"] = f"Bearer {GATEWAY_SECRET}"
        try:
            resp = _gateway_session.post(
                GATEWAY_URL, json=payload, headers=headers, timeout=10,
            )
            log(f"Reaction forwarded: {resp.status_code}")
        except Exception as e:
            log(f"Failed to forward reaction: {e}")
        return

    if not data_msg.get("message") and not data_msg.get("attachments"):
        return

    sender = envelope.get("source") or envelope.get("sourceNumber")
    if not sender:
        return

    message = data_msg.get("message", "")
    timestamp = data_msg.get("timestamp") or envelope.get("timestamp", 0)

    attachments = []
    for att in data_msg.get("attachments", []):
        attachments.append({
            "contentType": att.get("contentType", ""),
            "filename": att.get("filename", ""),
            "id": att.get("id", ""),
            "size": att.get("size", 0),
        })

    att_info = f", {len(attachments)} attachment(s)" if attachments else ""
    log(f"Incoming message from {sender[-4:]} ({len(message)} chars{att_info})")

    payload = {
        "sender": sender,
        "message": message,
        "timestamp": timestamp,
        "attachments": attachments,
    }
    headers = {}
    if GATEWAY_SECRET:
        headers["Authorization"] = f"Bearer {GATEWAY_SECRET}"

    try:
        resp = _gateway_session.post(
            GATEWAY_URL, json=payload, headers=headers, timeout=30,
        )
        log(f"Forwarded to gateway: {resp.status_code}")
    except Exception as e:
        log(f"Failed to forward: {e}")


_LOCATION_FILE = "/tmp/botarmy-location.json"
_LOCATION_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "location-helper")
_LOCATION_INTERVAL = 1800  # 30 minutes
_last_location_probe = 0.0


def _probe_location():
    """Try to get location via CoreLocation helper and write to shared file.

    Best-effort: if helper binary doesn't exist or fails, silently skip.
    """
    global _last_location_probe
    now = time.time()
    if now - _last_location_probe < _LOCATION_INTERVAL:
        return
    _last_location_probe = now

    if not os.path.exists(_LOCATION_HELPER):
        return

    try:
        import subprocess
        result = subprocess.run(
            [_LOCATION_HELPER],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout.strip())
            if "lat" in data and "lon" in data:
                with open(_LOCATION_FILE, "w") as f:
                    json.dump(data, f)
                log(f"Location updated: {data.get('lat', '?'):.4f}, {data.get('lon', '?'):.4f} "
                    f"(±{data.get('accuracy', '?')}m)")
    except Exception as e:
        log(f"Location probe failed (non-fatal): {e}")


def poll_loop():
    """Poll signal-cli for messages and forward them.

    Monitors connection health and reconnects if signal-cli becomes unresponsive.
    """
    log(f"Polling signal-cli at {SIGNAL_CLI_URL} every ~1.5s")
    log(f"Forwarding to {GATEWAY_URL}")
    _consecutive_errors = 0
    _MAX_ERRORS = 60  # ~30s of consecutive connection failures triggers reconnect

    while True:
        # Periodic location probe (every 30 min, non-blocking)
        try:
            _probe_location()
        except Exception:
            pass

        messages = _receive_messages()
        if messages is None:
            # Connection error — signal-cli may be down
            _consecutive_errors += 1
        elif messages:
            _consecutive_errors = 0
            for msg in messages:
                envelope = msg.get("envelope", msg)
                try:
                    _process_envelope(envelope)
                except Exception as e:
                    log(f"Error processing envelope: {e}")
        else:
            # Empty list — normal, no new messages
            _consecutive_errors = 0

        # Reconnect if signal-cli has been unresponsive for ~30s
        if _consecutive_errors >= _MAX_ERRORS:
            log(f"signal-cli unresponsive ({_consecutive_errors} consecutive errors)")
            _wait_for_signal_cli()
            _consecutive_errors = 0

        # Gap between polls — signal-cli needs a brief pause to release the lock
        time.sleep(0.5)


def main():
    if not GATEWAY_SECRET:
        log("WARNING: GATEWAY_SECRET not set — requests will be rejected by gateway")

    log("Waiting for signal-cli...")
    while True:
        try:
            resp = _signal_session.post(
                SIGNAL_CLI_URL.rstrip("/") + "/api/v1/rpc",
                json={"jsonrpc": "2.0", "id": 1, "method": "version"},
                timeout=5,
            )
            version = resp.json().get("result", {}).get("version", "?")
            log(f"signal-cli v{version} is ready")
            break
        except Exception:
            time.sleep(3)

    poll_loop()


if __name__ == "__main__":
    main()
