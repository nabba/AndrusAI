import hmac
import logging
import asyncio

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.config import get_settings
from app.security import is_authorized_sender, is_within_rate_limit
from app.signal_client import SignalClient
from app.agents.commander import Commander
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

settings = get_settings()
logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

MAX_MESSAGE_LENGTH = 4000  # Prevent abuse / token bombing


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.crews.self_improvement_crew import SelfImprovementCrew

    trigger = CronTrigger.from_crontab(settings.self_improve_cron)
    scheduler.add_job(SelfImprovementCrew().run, trigger)
    scheduler.start()
    logger.info("CrewAI Agent Team started")
    yield
    scheduler.shutdown()


app = FastAPI(title="CrewAI Agent Gateway", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1"],
    allow_methods=["POST"],
    allow_headers=["Content-Type", "Authorization"],
)

signal_client = SignalClient()
commander = Commander()


def _verify_gateway_secret(request: Request) -> bool:
    """Verify the forwarder is authenticated with the gateway secret."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    token = auth[7:]
    return hmac.compare_digest(token, settings.gateway_secret)


@app.post("/signal/inbound")
async def receive_signal(request: Request):
    # Authenticate the request source
    if not _verify_gateway_secret(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    payload = await request.json()
    sender = payload.get("sender", "")
    text = payload.get("message", "").strip()

    if not is_authorized_sender(sender):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not is_within_rate_limit(sender):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    if not text:
        return {"status": "ignored"}

    # Enforce message length limit
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH]

    asyncio.create_task(handle_task(sender, text))
    return {"status": "accepted"}


async def handle_task(sender: str, text: str):
    try:
        result = await asyncio.to_thread(commander.handle, text)
        await signal_client.send(sender, result)
    except Exception:
        logger.exception("Error handling task")
        # Generic error — do not leak internals to Signal
        await signal_client.send(sender, "Sorry, something went wrong processing your request. Please try again.")


@app.get("/health")
async def health():
    return {"status": "ok"}
