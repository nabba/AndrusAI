import socket
import json
import asyncio
from app.config import get_settings
import logging

settings = get_settings()
logger = logging.getLogger(__name__)

MAX_SIGNAL_LENGTH = 1500


class SignalClient:
    async def send(self, recipient: str, text: str):
        """Send a message back to the user's iPhone via signal-cli JSON-RPC socket."""
        # Security: only allow sending to the owner's number
        if recipient.strip() != settings.signal_owner_number.strip():
            logger.error("Blocked attempt to send to non-owner recipient")
            return

        chunks = [
            text[i : i + MAX_SIGNAL_LENGTH]
            for i in range(0, len(text), MAX_SIGNAL_LENGTH)
        ]
        for chunk in chunks:
            await asyncio.to_thread(self._send_sync, recipient, chunk)

    def _send_sync(self, recipient: str, text: str):
        sock = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(settings.signal_socket_path)
            sock.settimeout(10)

            request = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "send",
                "params": {
                    "recipient": [recipient],
                    "message": text,
                },
            }) + "\n"

            sock.sendall(request.encode())

            data = b""
            while b"\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk

            if data:
                resp = json.loads(data.split(b"\n")[0])
                if "error" in resp:
                    logger.error("signal-cli RPC error")
                else:
                    logger.info("Message sent successfully")
            else:
                logger.error("No response from signal-cli socket")

        except Exception as e:
            logger.error(f"signal-cli send error: {type(e).__name__}")
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
