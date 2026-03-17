from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.config import get_settings
from app.security import is_authorized_sender, is_within_rate_limit
from app.signal_client import SignalClient
from app.agents.commander import Commander
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import logging
import asyncio

settings = get_settings()
logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start self-improvement scheduler
    from app.crews.self_improvement_crew import SelfImprovementCrew

    trigger = CronTrigger.from_crontab(settings.self_improve_cron)
    scheduler.add_job(SelfImprovementCrew().run, trigger)
    scheduler.start()
    logger.info("CrewAI Agent Team started")
    yield
    scheduler.shutdown()


app = FastAPI(title="CrewAI Agent Gateway", lifespan=lifespan)

# CORS: loopback only — no browser access from external origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1"],
    allow_methods=["POST"],
    allow_headers=["*"],
)

signal_client = SignalClient()
commander = Commander()


@app.post("/signal/inbound")
async def receive_signal(request: Request):
    payload = await request.json()
    sender = payload.get("sender", "")
    text = payload.get("message", "").strip()

    if not is_authorized_sender(sender):
        raise HTTPException(status_code=403, detail="Unauthorized sender")
    if not is_within_rate_limit(sender):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    if not text:
        return {"status": "ignored"}

    # Dispatch to Commander asynchronously so Signal doesn't time out
    asyncio.create_task(handle_task(sender, text))
    return {"status": "accepted"}


async def handle_task(sender: str, text: str):
    try:
        result = await asyncio.to_thread(commander.handle, text)
        await signal_client.send(sender, result)
    except Exception as e:
        logger.exception("Error handling task")
        await signal_client.send(sender, f"Error: {str(e)[:200]}")


@app.get("/health")
async def health():
    return {"status": "ok"}
