import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base
from routers import ghl, twilio, auth, activity
from routers.auth import get_current_user
from routers import booking, demo, dashboard, admin
from services.ghl_oauth import start_token_refresh_loop

# Configure logging for production
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("airec")

# Create all database tables
Base.metadata.create_all(bind=engine)

_refresh_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _refresh_task
    # Start GHL V2 OAuth token auto-refresh loop in the background
    _refresh_task = asyncio.create_task(start_token_refresh_loop())
    logger.info("🔄 [Startup] GHL OAuth token refresh loop started.")
    yield
    # Shutdown: cancel the background refresh task cleanly
    if _refresh_task and not _refresh_task.done():
        _refresh_task.cancel()
        logger.info("🛑 [Shutdown] GHL OAuth token refresh loop stopped.")

app = FastAPI(title="AI Receptionist API", version="1.0.0", lifespan=lifespan)

# Setup CORS for production frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Update this in production to specific domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth.router)
app.include_router(ghl.router, dependencies=[Depends(get_current_user)])
app.include_router(twilio.router)
app.include_router(booking.router)
app.include_router(demo.router)
app.include_router(dashboard.router, dependencies=[Depends(get_current_user)])
app.include_router(admin.router)

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/demo")
async def demo_page():
    return FileResponse("static/demo.html")

@app.get("/", response_class=HTMLResponse)
def read_root():
    return FileResponse("static/index.html")

@app.get("/health")
def health_check():
    return {"status": "ok"}
