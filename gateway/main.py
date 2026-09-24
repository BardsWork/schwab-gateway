"""FastAPI application entry point."""
import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from gateway import client_state
from gateway.monitoring.token_monitor import run_token_monitor
from gateway.routers import accounts, auth, bars, health, instruments, llm_docs, options, quotes, stream
from gateway.settings import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialise Schwab client + start background token monitor."""
    client_state.init_client()
    monitor_task = asyncio.create_task(run_token_monitor())
    logger.info("schwab-gateway started.")
    try:
        yield
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        logger.info("schwab-gateway stopped.")


app = FastAPI(
    title="schwab-gateway",
    description=(
        "Local HTTP proxy for Schwab market data. Centralises OAuth token management so "
        "every project on the local network can call market-data endpoints without holding "
        "credentials or managing token files.\n\n"
        "**Base URL:** `http://localhost:8182`\n\n"
        "**All data endpoints return `503`** until the reauth flow is completed "
        "(see the Auth section).\n\n"
        "**Machine-readable API reference for AI agents:** `GET /llm-docs`"
    ),
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(accounts.router)
app.include_router(bars.router)
app.include_router(options.router)
app.include_router(auth.router)
app.include_router(instruments.router)
app.include_router(quotes.router)
app.include_router(stream.router)
app.include_router(llm_docs.router)


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "gateway.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
