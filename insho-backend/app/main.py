from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.api.v1.router import api_router

app = FastAPI(
    title="INSHO API",
    description="Backend API for the INSHO Event Design Intelligence Platform",
    version="1.0.0"
)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    try:
        return await request_validation_exception_handler(request, exc)
    except ValueError:
        # Invalid numeric input may itself be impossible to serialize as JSON.
        return JSONResponse(status_code=422, content={"detail": [
            {key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()
        ]})


@app.get("/")
def root():
    return {
        "message": "Welcome to INSHO API",
        "status": "running"
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "application": "INSHO"
    }

app.include_router(api_router, prefix="/api/v1")

# The workspace and API share an origin and the existing bearer-session service.
workspace_files = Path(__file__).resolve().parent / "web"
app.mount("/workspace/static", StaticFiles(directory=workspace_files / "spatial"), name="workspace-static")


@app.get("/workspace", include_in_schema=False)
@app.get("/workspace/projects/{project_id}/spatial", include_in_schema=False)
@app.get("/workspace/projects/{project_id}/spatial/{scene_id}", include_in_schema=False)
def workspace(project_id: Optional[UUID] = None, scene_id: Optional[UUID] = None):
    return FileResponse(workspace_files / "index.html", headers={
        "Cache-Control": "no-cache",
        "Content-Security-Policy": "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; style-src 'self'",
        "X-Content-Type-Options": "nosniff",
    })
