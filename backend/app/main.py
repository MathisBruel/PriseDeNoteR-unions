import logging

from fastapi import FastAPI

from app.api import auth, google, meetings, settings

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="PriseDeNoteRéunions", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.include_router(auth.router)
app.include_router(settings.router)
app.include_router(google.router)
app.include_router(meetings.router)
app.include_router(meetings.ws_router)


@app.get("/api/health")
async def health():
    return {"ok": True}
