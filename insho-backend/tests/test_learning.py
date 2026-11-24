import json
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.event import Event
from app.services.ai_orchestration import AIProviderError, ProviderResult, ai_execution_service
from test_assets import auth, upload_jpeg
from test_design_versioning import Versions, versions
from test_phase9_spatial import create_scene, make_project
from test_presentation import concept, present
from test_production import ProductionAPI, production, request
from test_production_bom import manual
from test_production_procurement import sourcing
from test_project_memory_history import make_memory


PLAN = {"start": "2026-10-10T11:00:00+05:30", "end": "2026-10-10T12:00:00+05:30"}
ACTUAL = {"start": "2026-10-10T11:30:00+05:30", "end": "2026-10-10T13:00:00+05:30"}


def call(versions, verb, route, status=200, **kwargs):
    return versions.call(verb, "/learning" + route, status, **kwargs)


def draft(versions, kind, version=None, *, event=None, status=201, assets=(), source="Verified field register 17",
          audit_id=None, supersedes=None, **data):
    if kind in {"cost", "quantity", "labour", "installation", "wastage"}:
        data.setdefault("coverage", "complete")
    return call(versions, "POST", "/actuals", status, json={
        "event_id": str(event or versions.event.id), "design_version_id": version["id"] if version is not None else None,
        "origin": "reported", "source_reference": source, "asset_ids": list(assets), "audit_id": audit_id,
        "supersedes_id": supersedes, "data": {"kind": kind, **data},
    })


def verify(versions, actual, status=200, **kwargs):
    return call(versions, "POST", f"/actuals/{actual['id']}/verify", status,
                json={"expected_revision": actual["revision"], "review_note": "Reviewed against the attributed evidence."}, **kwargs)


def reconciliation(versions, version, *estimates):
    return call(versions, "POST", "/reconciliations", 201, json={
        "event_id": str(versions.event.id), "design_version_id": version["id"], "estimates": list(estimates),
    })


def workflow(versions):
    predecessors = str(uuid4())
    installed = str(uuid4())
    plan = request(versions.api.client, "PUT", versions.api.path + "/workflow/plan", json={
        "expected_version": 0, "tasks": [
            {"id": predecessors, "name": "Prepare panels", "kind": "fabrication", "duration_minutes": 20},
            {"id": installed, "name": "Install panels", "kind": "installation", "duration_minutes": 60,
             "window_start": PLAN["start"], "window_end": PLAN["end"], "depends_on": [predecessors]},
        ],
    })
    return {"record_id": plan["record_id"], "revision": plan["version"], "task_id": installed}, predecessors


def test_frozen_bom_budget_reconciliation_and_corrected_actuals(versions, sourcing):
    bom, material, *_ = sourcing.make_bom(extras=(manual(category="equipment", quantity="1", unit_rate="0"),))
    estimates = [line for line in bom["lines"] if line["basis"] == "estimated"]
    line = next(row for row in estimates if row["category"] == "material")
    zero = next(row for row in estimates if row["category"] == "equipment")
    budget = sourcing.call("POST", "/budgets", 201, params=versions.event_params, json={
        "expected_version": 0, "currency": "INR", "amount": "200", "bom_ids": [bom["id"]],
    })
    sourcing.call("POST", f"/budgets/{budget['id']}/approve", json={"expected_version": 1})
    version = versions.save(selection={"bom_ids": [bom["id"]], "budget_ids": [budget["id"]]})
    budget_basis = {"kind": "budget", "id": budget["id"]}
    prior = reconciliation(versions, version, {"metric": "cost", "cost": budget_basis})
    assert prior["comparisons"][0]["status"] == "missing_actual" and prior["comparisons"][0]["actual"] is None

    costs = {"baseline": budget_basis, "category": "budget_total", "amount": "89.1234", "currency": "INR", "coverage": "complete",
             "invoice_reference": "INV-19", "spent_on": "2026-10-10"}
    draft_cost = draft(versions, "cost", version, **costs)
    pending = reconciliation(versions, version)
    assert pending["comparisons"][0]["status"] == "unverified_actual" and pending["comparisons"][0]["actual"] is None
    path = f"/actuals/{draft_cost['id']}"
    changed = call(versions, "PATCH", path, json={"expected_revision": 1, "origin": "reported", "source_reference": "Invoice 19",
                                                   "data": {"kind": "cost", **costs, "amount": "90.1234"}})
    call(versions, "PATCH", path, 409, json={"expected_revision": 1, "origin": "reported", "data": {"kind": "cost", **costs}})
    verified = verify(versions, changed)
    assert verified["revision"] == 3 and verified["verified_by_id"] == str(versions.owner["user"].id)
    verify(versions, changed, 409)
    verify(versions, draft(versions, "quantity", version, bom_line_id=line["id"], material_id=material["id"],
                           consumed={"value": "350000", "unit": "cm2"}, wastage={"value": "1.25", "unit": "m2"}))
    verify(versions, draft(versions, "cost", version, **{**costs, "baseline": {"kind": "bom_line", "id": zero["id"]},
                                                       "amount": "1", "category": "equipment"}))
    report = reconciliation(versions, version)
    by_category = {item["category"]: item for item in report["comparisons"]}
    money = by_category["budget_total"]
    assert Decimal(money["estimate"]) == 80 and Decimal(money["actual"]) == Decimal("90.1234")
    assert Decimal(money["difference"]) == Decimal("10.1234") and money["percentage_difference"] == "12.6543"
    assert by_category["equipment"]["estimate"] == "0" and by_category["equipment"]["percentage_difference"] is None
    qty = by_category["consumption"]
    assert Decimal(qty["estimate"]) == 32 and Decimal(qty["actual_in_estimate_units"]) == 35
    assert qty["conversion_method"] == "phase10_production_unit_conversion"
    assert reconciliation(versions, version)["id"] == report["id"]
    sourcing.parts.revise(manual(category="equipment", quantity="2", unit_rate="10"))
    assert reconciliation(versions, version)["id"] == report["id"]
    correction = verify(versions, draft(versions, "cost", version, supersedes=verified["id"], **{**costs, "amount": "95"}))
    after = reconciliation(versions, version)
    assert after["id"] != report["id"] and call(versions, "GET", "/reconciliations/" + report["id"]) == report
    costs_only = call(versions, "GET", f"/reconciliations/{after['id']}/comparisons", params={"metric": "cost"})
    assert next(item for item in costs_only if item["category"] == "budget_total")["verified_actual_id"] == correction["id"]
    assert len(call(versions, "GET", "/reconciliations", params=versions.event_params)) == 4


def test_units_currency_partial_and_ambiguous_are_explicit(versions, sourcing):
    bom, material, *_ = sourcing.make_bom()
    version = versions.save(selection={"bom_ids": [bom["id"]]})
    line = bom["lines"][0]
    quantity = {"bom_line_id": line["id"], "material_id": material["id"], "consumed": {"value": "12", "unit": "kg"}}
    draft(versions, "quantity", version, status=422, **{**quantity, "consumed": {"value": "1.3", "unit": "each"}})
    draft(versions, "quantity", version, status=422, **quantity, wastage={"value": "0.1", "unit": "m2"})
    qty = verify(versions, draft(versions, "quantity", version, **quantity))
    cost = {"baseline": {"kind": "bom_line", "id": line["id"]}, "category": line["category"], "amount": "10",
            "currency": "USD", "spent_on": "2026-10-10"}
    verify(versions, draft(versions, "cost", version, coverage="partial", **cost))
    assert {item["status"] for item in reconciliation(versions, version)["comparisons"]} == {"partial_actual", "incompatible_unit"}
    verify(versions, draft(versions, "cost", version, **cost))
    assert {item["status"] for item in reconciliation(versions, version)["comparisons"]} == {"currency_mismatch", "incompatible_unit"}
    verify(versions, draft(versions, "cost", version, **cost))
    assert {item["status"] for item in reconciliation(versions, version)["comparisons"]} == {"ambiguous_actual", "incompatible_unit"}
    assert call(versions, "DELETE", f"/actuals/{qty['id']}", 409, params={"expected_revision": 2})["detail"]["code"] == "learning_actual_final"


def test_labour_installation_wastage_and_delay(versions, sourcing):
    activity, dependency = workflow(versions)
    version = versions.save()
    material = sourcing.parts.material()
    draft(versions, "labour", version, status=422, activity=activity, category="installation", work_window=ACTUAL,
          hours="1.5", hours_basis="per_worker")
    labour = draft(versions, "labour", version, activity=activity, category="installation", work_window=ACTUAL,
                   hours="1.5", hours_basis="per_worker", worker_count=4,
                   planned_team_hours={"value": "4", "source_reference": "Approved roster R-12"})
    assert verify(versions, labour)["derived"]["actual"] == "6"
    installation = verify(versions, draft(versions, "installation", version, activity=activity, installed=ACTUAL))
    assert Decimal(installation["derived"]["duration_seconds"]) == 5400
    assert Decimal(installation["derived"]["completion_delay_seconds"]) == 3600
    assert installation["derived"]["depends_on"] == [dependency]
    verify(versions, draft(versions, "wastage", version, material_id=material["id"], activity=activity, category="cuttings",
                           wasted={"value": "2", "unit": "m2"}, expected={"value": "0", "source_reference": "Material plan"},
                           expected_unit="m2"))
    delay = verify(versions, draft(versions, "delay", activity=activity, actual=ACTUAL, impact="Sequencing delayed",
                                   reason={"observation": "Reported loading delay."}))
    assert delay["derived"]["reason_source_status"] == "unconfirmed"
    assert delay["derived"]["start_delay_seconds"] == "1800"
    checks = {item["metric"]: item for item in reconciliation(versions, version)["comparisons"]}
    assert Decimal(checks["labour"]["difference"]) == 2
    assert Decimal(checks["installation"]["difference"]) == 1800 and checks["installation"]["percentage_difference"] == "50.0000"
    assert checks["wastage"]["percentage_difference"] is None


def test_vendor_client_and_incident_capture(versions, sourcing):
    activity, _ = workflow(versions)
    version = versions.save()
    venue = request(versions.api.client, "POST", "/api/v1/venues", 201, json={
        "name": "Field site", "organization_id": str(versions.owner["organization"].id),
    })
    event_path = f"/api/v1/events/{versions.event.id}"
    request(versions.api.client, "PATCH", event_path, json={"venue_id": venue["id"], "status": "scheduled"})
    rain = verify(versions, draft(versions, "weather_incident", category="rain", occurred_at=ACTUAL["start"],
                                  venue_id=venue["id"], impact="Delayed delivery", activities=[activity]))
    production_incident = verify(versions, draft(versions, "production_incident", category="delivery", description="Panel delivery postponed",
                                                 impact="disruptive", occurred_at=ACTUAL["start"], reported_at=ACTUAL["end"],
                                                 activities=[activity], cause={"observation": "Warehouse report", "source_reference": "dispatch ref."}))
    assert {source["kind"] for source in rain["sources"]} == {"venue", "workflow_task"}
    supplier = sourcing.supplier()
    sourcing.call("PUT", f"/suppliers/{supplier['id']}", json={"expected_version": 0, "active": True})
    vendor = verify(versions, draft(versions, "vendor", supplier_id=supplier["id"], period=ACTUAL, dimension="delivery_timing",
                                    outcome="missed", observation="Delivery slipped", incident_ids=[production_incident["id"]]))
    assert [item["id"] for item in call(versions, "GET", "/actuals", params={**versions.event_params, "kind": "vendor",
                                                                           "supplier_id": supplier["id"]})] == [vendor["id"]]
    feedback = draft(versions, "client_feedback", version, category="arrival", written_feedback="The arrival was quiet.",
                     rating="4", rating_scale_max="5", feedback_source="Verbatim client debrief")
    verify(versions, feedback, 409)
    request(versions.api.client, "PATCH", event_path, json={"status": "completed"})
    assert verify(versions, feedback)["data"]["feedback_source"] == "Verbatim client debrief"
    change = verify(versions, draft(versions, "change_order", version, original_scope="One partition", changed_scope="Two partitions",
                                     requested_at=ACTUAL["start"], requester_reference="Client representative", approval_status="approved",
                                     approval_reference="Signed CO-11", reason={"observation": "Client requested divider", "source_reference": "CO-11"},
                                     reason_category="client_request", cost_impact="71.1234", currency="INR", schedule_impact_minutes=30,
                                     activities=[activity], quantity_impact="1", quantity_unit="each"))
    assert change["data"]["cost_impact"] == "71.1234" and change["derived"]["reason_source_status"] == "attributed"


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_concept_decisions_use_checkpoint_memory_and_audit(versions, present, concept, decision):
    dna, _, sections = concept
    sheet = present.create(purpose="concept", sections=sections)
    pin = {"id": sheet["id"], "revision": sheet["revision"]}
    design = versions.save(selection={"design_dna_id": str(dna.id), "presentation": pin})
    versions.call("PUT", f"/design-versions/{design['id']}/review", json=pin)
    checkpoint = present.checkpoint(sheet)
    present.review(sheet, checkpoint)
    draft(versions, "concept_decision", design, status=409, checkpoint_id=checkpoint["id"])
    result = present.review(sheet, checkpoint, "decision", decision=decision, feedback="Recorded decision detail")
    run = versions.call("POST", "/design-audits", 201, json={"design_version_id": design["id"]})
    memory = make_memory(versions, decision, verification="verified", source_reference="Checkpoint",
                         linked_version_id=design["id"], decision_id=checkpoint["id"])
    actual = verify(versions, draft(versions, "concept_decision", design, checkpoint_id=checkpoint["id"],
                                    memory_item_id=memory["id"], audit_id=run["id"], source=None))
    assert actual["derived"]["decision"] == decision
    assert actual["derived"]["decided_by_id"] == result["decided_by_user_id"]
    assert {item["kind"] for item in actual["sources"]} == {
        "design_version", "presentation", "presentation_checkpoint", "design_audit", "project_memory",
    }


def test_scope_permissions_and_media_redaction(versions, db_session, identity_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "learning-assets"))
    asset_id = upload_jpeg(versions.api.client, versions.owner, versions.project.id)["asset_id"]
    outsider = identity_factory()
    other_project = make_project(db_session, outsider)
    private_asset = upload_jpeg(versions.api.client, outsider, other_project.id)["asset_id"]
    draft(versions, "final_media", status=404, asset_id=private_asset, description="Final installation")
    media = draft(versions, "final_media", asset_id=asset_id, description="Final installation", source=None)
    member = auth(identity_factory(role="member", organization=versions.owner["organization"]))
    editor = auth(identity_factory(role="designer", organization=versions.owner["organization"]))
    verify(versions, media, 403, headers=editor)
    path = "/actuals/" + media["id"]
    verify(versions, media)
    assert call(versions, "GET", path, headers=member)["available_assets"] == [asset_id]
    call(versions, "GET", path, 404, headers=auth(outsider))
    call(versions, "GET", path, 401, headers={"Authorization": ""})
    call(versions, "PATCH", path, 403, headers=member, json={"expected_revision": 2, "origin": "observed",
                                                           "data": {"kind": "final_media", "asset_id": asset_id, "description": "Other"}})
    request(versions.api.client, "DELETE", f"/api/v1/assets/{asset_id}")
    after = call(versions, "GET", path)
    assert not after["available_assets"] and "asset_id" not in after["data"] and "asset_metadata" not in after["derived"]
    draft_only = draft(versions, "production_incident", category="operation", description="Entered in error",
                       occurred_at=ACTUAL["start"], reported_at=ACTUAL["end"])
    call(versions, "DELETE", f"/actuals/{draft_only['id']}", 204, params={"expected_revision": 1})
    call(versions, "GET", f"/actuals/{draft_only['id']}", 404)


class StubReview:
    mode = "failure"

    async def execute(self, model, request, timeout):
        items = json.loads(request.input)
        assert set(items[0]) == {"insight_id", "category", "sample_count", "eligible_event_count", "evidence_ids"}
        if self.mode == "failure":
            raise AIProviderError("provider_unavailable", "Test outage", retryable=True)
        evidence = str(uuid4()) if self.mode == "invalid" else items[0]["evidence_ids"][0]
        return ProviderResult(output=json.dumps({"reviews": [{
            "insight_id": items[0]["insight_id"], "evidence_id": evidence, "emphasis": "review_execution",
        }]}))


def test_repeated_vendor_insights_provenance_disposition_and_ai(versions, sourcing, db_session, monkeypatch):
    supplier = sourcing.supplier()
    sourcing.call("PUT", f"/suppliers/{supplier['id']}", json={"expected_version": 0, "active": True})
    other = Event(project_id=versions.project.id, organization_id=versions.owner["organization"].id, name="Second comparable event")
    db_session.add(other)
    db_session.commit()
    vendor = {"supplier_id": supplier["id"], "period": ACTUAL, "dimension": "delivery_timing",
              "outcome": "missed", "observation": "Private shipping note."}
    first = verify(versions, draft(versions, "vendor", **vendor))
    verify(versions, draft(versions, "vendor", **vendor))
    assert call(versions, "POST", "/insights", 201, json={"event_ids": [str(versions.event.id)]})["insights"] == []
    other_draft = draft(versions, "vendor", event=other.id, **vendor)
    body = {"event_ids": [str(versions.event.id), str(other.id)]}
    assert call(versions, "POST", "/insights", 201, json=body)["insights"] == []
    verify(versions, other_draft)
    report = call(versions, "POST", "/insights", 201, json=body)
    [item] = report["insights"]
    assert item["sample_count"] == 2 and item["eligible_event_count"] == 2 and item["confidence"] == "limited"
    assert first["id"] in {ref["id"] for ref in item["sources"]} and item["human_review_required"]
    base = f"/insights/{report['id']}"
    assert call(versions, "GET", base + "/evidence")
    item_path = f"{base}/items/{item['id']}"
    assert call(versions, "GET", item_path) == item
    disposition = call(versions, "POST", item_path + "/dispositions", 201, json={"decision": "deferred", "note": "Await more data"})
    assert call(versions, "GET", item_path + "/dispositions") == [disposition]
    assert call(versions, "GET", base) == report

    for key, value in {"AI_OPENAI_ENABLED": "true", "OPENAI_API_KEY": "fixture", "AI_OPENAI_MODELS": "fixture-model",
                       "AI_OPENAI_CAPABILITIES": "text,structured_output", "AI_ANTHROPIC_ENABLED": "false", "AI_MAX_RETRIES": "0"}.items():
        monkeypatch.setenv(key, value)
    stub = StubReview()
    monkeypatch.setitem(ai_execution_service.adapters, "openai", stub)
    assist = {**body, "ai_assisted": True}
    failed = call(versions, "POST", "/insights", 201, json=assist)
    assert failed["insights"][0]["id"] == item["id"] and failed["warnings"] == ["ai_review_unavailable"]
    assert failed["insights"][0]["ai_origin"] is None
    stub.mode = "invalid"
    rejected = call(versions, "POST", "/insights", 201, json=assist)
    assert rejected["insights"][0]["id"] == item["id"] and rejected["warnings"] == ["ai_reviews_incomplete"]
    assert rejected["insights"][0]["ai_origin"] is None
    stub.mode = "valid"
    assist["idempotency_key"] = str(uuid4())
    inferred = call(versions, "POST", "/insights", 201, json=assist)
    assert inferred["insights"][0]["ai_origin"] == "ai_inferred" and inferred["ai_execution_id"]
    assert inferred["insights"][0]["ai_evidence_id"] in {ref["id"] for ref in item["sources"] if ref["kind"] == "actual"}
    assert inferred["insights"][0]["origin"] == "derived"
    assert call(versions, "POST", "/insights", 201, json=assist)["id"] == inferred["id"]


def test_quantitative_insights_use_current_verified_comparisons(versions, db_session):
    second = Event(project_id=versions.project.id, organization_id=versions.owner["organization"].id, name="Second build")
    db_session.add(second)
    db_session.commit()
    second_scene = create_scene(versions.api.client, versions.project.id, event_id=str(second.id))
    other = Versions((ProductionAPI(versions.api.client, second_scene), versions.owner, versions.project, second))
    primary_ref, _ = workflow(versions)
    other_ref, _ = workflow(other)
    primary_version = versions.save()
    other_version = other.save()
    draft(versions, "installation", other_version, status=404, activity=primary_ref, installed=ACTUAL)
    primary = verify(versions, draft(versions, "installation", primary_version, activity=primary_ref, installed=ACTUAL))
    verify(other, draft(other, "installation", other_version, activity=other_ref, installed=ACTUAL))
    reports = [reconciliation(versions, primary_version), reconciliation(other, other_version)]
    body = {"event_ids": [str(versions.event.id), str(second.id)], "reconciliation_ids": [row["id"] for row in reports]}
    historical = call(versions, "POST", "/insights", 201, json=body)
    [repeated] = historical["insights"]
    assert repeated["category"] == "installation" and repeated["sample_count"] == 2
    assert {row["id"] for row in reports} <= {item["id"] for item in repeated["sources"]}
    verify(versions, draft(versions, "installation", primary_version, activity=primary_ref, installed=PLAN, supersedes=primary["id"]))
    refreshed = call(versions, "POST", "/insights", 201, json=body)
    assert refreshed["insights"] == [] and "reconciliation_contains_superseded_or_unavailable_actual" in refreshed["warnings"]
    assert call(versions, "GET", f"/insights/{historical['id']}") == historical


def test_empty_report_retains_event_and_scope(versions, db_session, identity_factory):
    other_tenant = identity_factory()
    other_project = make_project(db_session, other_tenant)
    foreign = Event(project_id=other_project.id, organization_id=other_tenant["organization"].id, name="Foreign project event")
    db_session.add(foreign)
    db_session.commit()
    call(versions, "POST", "/insights", 404, json={"event_ids": [str(foreign.id)]})
    key = str(uuid4())
    body = {"event_ids": [str(versions.event.id)], "idempotency_key": key}
    report = call(versions, "POST", "/insights", 201, json=body)
    call(versions, "POST", "/insights", 409, json={**body, "ai_assisted": True})
    call(versions, "GET", f"/insights/{report['id']}", 404, headers=auth(other_tenant))
    archived = request(versions.api.client, "DELETE", f"/api/v1/events/{versions.event.id}", 409)
    assert archived["detail"]["code"] == "learning_history_retained"
