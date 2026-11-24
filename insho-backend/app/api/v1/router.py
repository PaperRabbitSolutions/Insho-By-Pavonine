from fastapi import APIRouter

from app.api.v1 import (
    auth,
    organizations,
    health,
    projects,
    event_type,
    events,
    venues,
    venue_zones,
    venue_sources,
    design_dna,
    project_memory,
    context,
    uploads,
    assets,
    object_library,
    ai,
    design_intelligence,
    spatial,
    spatial_design,
    production,
    production_procurement,
    presentations,
    design_versions,
    design_audits,
    learning,
)

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(organizations.router)
api_router.include_router(event_type.router)
api_router.include_router(events.router)
api_router.include_router(venues.router)
api_router.include_router(projects.router)
api_router.include_router(venue_zones.router)
api_router.include_router(venue_sources.router)
api_router.include_router(design_dna.router)
api_router.include_router(project_memory.router)
api_router.include_router(context.router)
api_router.include_router(uploads.router)
api_router.include_router(assets.router)
api_router.include_router(object_library.router)
api_router.include_router(ai.router)
api_router.include_router(design_intelligence.router)
api_router.include_router(spatial.router)
api_router.include_router(spatial_design.router)
api_router.include_router(production.router)
api_router.include_router(production_procurement.router)
api_router.include_router(presentations.router)
api_router.include_router(design_versions.router)
api_router.include_router(design_audits.router)
api_router.include_router(learning.router)
