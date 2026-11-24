import json
import uuid

import pytest

from app.api.v1.ai import execution_service
from app.models.design_artifact import DesignArtifact
from app.models.project import Project
from app.schemas.ai import AIExecutionRequest
from app.services.ai_orchestration import ProviderResult


MOCK_OUTPUTS = {
    "mood": {
        "atmosphere": "Warm garden evening",
        "emotional_direction": ["welcoming", "intimate"],
        "keywords": ["garden", "warm", "layered"],
        "color_story": "Muted green with warm ivory accents.",
        "colors": [
            {"name": "Moss", "hex": "#71806A", "role": "primary", "rationale": "Grounded garden tone."},
            {"name": "Ivory", "hex": "#F4EBDD", "role": "secondary", "rationale": "Soft warm contrast."},
        ],
        "floral_direction": ["seasonal loose forms"], "materials": ["linen"], "textures": ["woven"],
        "lighting_atmosphere": "Warm low-level ambient lighting.", "decor_and_styling": ["layered linens"],
        "visual_inspiration": ["garden at dusk"], "rationale": "Uses the supplied garden brief.",
        "uncertainties": [], "confidence": 0.8,
    },
    "style": {
        "creative_direction": "Contemporary garden supper.", "design_language": ["organic", "restrained"],
        "visual_identity": "Soft natural forms with clear focal hierarchy.", "architectural_language": ["light framing"],
        "forms_and_shapes": ["soft arches"], "motifs": ["leaf linework"], "composition_principles": ["layered foreground"],
        "spatial_character": "Open and intimate.", "guest_experience": "A calm arrival into a warm dining space.",
        "formality": "semi-formal", "luxury_level": None, "visual_hierarchy": ["entry", "dining", "focal floral"],
        "styling_principles": ["repeat materials"], "materials_and_finishes": ["linen", "brushed metal"],
        "lighting_character": "Warm ambient layers.", "photography_direction": "Capture layered depth.",
        "rationale": "Builds on the approved mood.", "conflicts": [],
    },
    "plan": {
        "summary": "A conceptual arrival, dining and service arrangement.",
        "zones": [{"name": "Dining", "zone_type": "seating", "purpose": "Shared dinner", "location_description": "Central area", "dimensions": None, "dimensions_source": "unknown", "capacity": None, "capacity_source": "unknown", "rationale": "Keeps dining central.", "safety_considerations": ["Confirm clear exits"]}],
        "adjacencies": [{"from": "Dining", "to": "Service", "relationship": "near"}],
        "guest_flow": ["Entry to dining"], "assumptions": ["Zones are conceptual"],
        "unknown_constraints": ["Venue dimensions not supplied"], "conflicts": [], "rationale": "Keeps guest and service routes legible.",
        "construction_accuracy": "conceptual_only",
    },
    "vision": {
        "concept_summary": "A consistent warm garden concept across views.",
        "views": [{"name": "Arrival view", "view": "front", "design_intent": "Show the arrival focal point.", "focal_points": ["Entry floral"], "consistency_notes": ["Use approved palette"], "image_generation_status": "not_requested"}],
        "based_on_modules": ["mood", "style", "plan"], "limitations": ["View plan only; no image rendered."],
    },
    "elements": {
        "elements": [{"name": "Seasonal entry floral", "category": "floral", "description": "Loose seasonal arrangement at arrival.", "design_role": "Welcome focal point", "related_zone": "Entry", "style": "Organic garden", "materials": ["Seasonal stems"], "color": "Muted greens and ivory", "dimensions": None, "quantity": None, "quantity_source": "unknown", "rationale": "Reinforces arrival mood.", "source_reference": None, "confidence": 0.7, "notes": ["Confirm seasonal availability"], "relationships": ["Entry zone"]}],
        "uncertainties": ["Dimensions and count are not specified."],
    },
}


class DesignMockAdapter:
    async def execute(self, model, request: AIExecutionRequest, timeout):
        module = request.task.removeprefix("design_")
        data = MOCK_OUTPUTS[module]
        return ProviderResult(output=json.dumps(data), usage=None, metadata={"test_mock": True})


class InvalidDesignMockAdapter:
    async def execute(self, model, request, timeout):
        return ProviderResult(output='{"unrelated":"not a mood sheet"}')


def make_project(db_session, identity, name="Design intelligence project"):
    project = Project(organization_id=identity["organization"].id, workspace_id=identity["workspace"].id, name=name, status="draft")
    db_session.add(project)
    db_session.commit()
    return project


def configure_mock_provider(monkeypatch, adapter):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-mock-key")
    monkeypatch.setenv("AI_OPENAI_ENABLED", "true")
    monkeypatch.setenv("AI_OPENAI_MODELS", "phase8-test-model")
    monkeypatch.setenv("AI_OPENAI_CAPABILITIES", "text,structured_output")
    monkeypatch.setattr(execution_service, "adapters", {"openai": adapter})


@pytest.mark.parametrize("module", ["mood", "style", "plan", "vision", "elements"])
def test_each_design_module_generates_persisted_structured_output(client, db_session, identity_factory, monkeypatch, module):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = make_project(db_session, identity)
    configure_mock_provider(monkeypatch, DesignMockAdapter())
    response = client.post(f"/api/v1/design-intelligence/projects/{project.id}/{module}/generate", json={})
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["module"] == module
    assert result["version"] == 1
    assert result["status"] == "draft"
    assert result["ai_execution_id"]
    if module == "plan":
        assert result["content"]["construction_accuracy"] == "conceptual_only"
        assert result["content"]["zones"][0]["dimensions"] is None
    if module == "vision":
        assert result["content"]["views"][0]["image_generation_status"] == "not_requested"
    if module == "elements":
        listed = client.get(f"/api/v1/design-intelligence/artifacts/{result['id']}/elements")
        assert listed.status_code == 200
        assert listed.json()[0]["source"] == "ai_suggested"


def test_upstream_changes_mark_downstream_stale_and_keep_versions(client, db_session, identity_factory, monkeypatch):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = make_project(db_session, identity)
    configure_mock_provider(monkeypatch, DesignMockAdapter())
    mood_url = f"/api/v1/design-intelligence/projects/{project.id}/mood/generate"
    style_url = f"/api/v1/design-intelligence/projects/{project.id}/style/generate"
    first_mood = client.post(mood_url, json={}).json()
    style = client.post(style_url, json={}).json()
    assert style["based_on"]["mood"]["artifact_id"] == first_mood["id"]
    second_mood = client.post(mood_url, json={}).json()
    assert second_mood["version"] == 2
    retrieved = client.get(f"/api/v1/design-intelligence/artifacts/{style['id']}")
    assert retrieved.json()["context_stale"] is True
    history = client.get(f"/api/v1/design-intelligence/projects/{project.id}/mood?history=true")
    assert [item["version"] for item in history.json()] == [2, 1]
    edited = client.put(f"/api/v1/design-intelligence/artifacts/{second_mood['id']}", json={"content": MOCK_OUTPUTS["mood"]})
    assert edited.status_code == 200
    assert edited.json()["version"] == 3
    approved = client.post(f"/api/v1/design-intelligence/artifacts/{edited.json()['id']}/approve")
    assert approved.json()["status"] == "approved"


def test_elements_are_individual_suggestions_and_can_be_confirmed(client, db_session, identity_factory, monkeypatch):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = make_project(db_session, identity)
    configure_mock_provider(monkeypatch, DesignMockAdapter())
    result = client.post(f"/api/v1/design-intelligence/projects/{project.id}/elements/generate", json={}).json()
    element = client.get(f"/api/v1/design-intelligence/artifacts/{result['id']}/elements").json()[0]
    confirmed = client.patch(f"/api/v1/design-intelligence/elements/{element['id']}", json={"confirmed": True})
    assert confirmed.status_code == 200
    assert confirmed.json()["source"] == "user_confirmed"
    assert confirmed.json()["confirmed_by_user_id"] == str(identity["user"].id)


def test_invalid_design_output_is_rejected_without_artifact(client, db_session, identity_factory, monkeypatch):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = make_project(db_session, identity)
    configure_mock_provider(monkeypatch, InvalidDesignMockAdapter())
    response = client.post(f"/api/v1/design-intelligence/projects/{project.id}/mood/generate", json={})
    assert response.status_code == 502
    assert db_session.query(DesignArtifact).filter(DesignArtifact.project_id == project.id).count() == 0


def test_generation_enforces_project_tenant_boundary(client, db_session, identity_factory, monkeypatch):
    other = identity_factory()
    project = make_project(db_session, other)
    configure_mock_provider(monkeypatch, DesignMockAdapter())
    response = client.post(f"/api/v1/design-intelligence/projects/{project.id}/mood/generate", json={})
    assert response.status_code == 404
