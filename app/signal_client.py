import subprocess
import asyncio
from app.config import get_settings
import logging

settings = get_settings()
logger = logging.getLogger(__name__)

MAX_SIGNAL_LENGTH = 1500


class SignalClient:
    async def send(self, recipient: str, text: str):
        """Send a message back to the user's iPhone via signal-cli."""
        # Split long messages into chunks
        chunks = [
            text[i : i + MAX_SIGNAL_LENGTH]
            for i in range(0, len(text), MAX_SIGNAL_LENGTH)
        ]
        for chunk in chunks:
            await asyncio.to_thread(self._send_sync, recipient, chunk)

    def _send_sync(self, recipient: str, text: str):
        cmd = [
            settings.signal_cli_path,
            "-a",
            settings.signal_bot_number,
            "send",
            "-m",
            text,
            recipient,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            logger.error(f"signal-cli error: {result.stderr}")
