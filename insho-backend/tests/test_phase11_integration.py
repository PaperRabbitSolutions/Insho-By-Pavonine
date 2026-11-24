"""Exercise Phase 11's public workflows on the schema built by Alembic."""

import logging
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db import database
from app.models.ai_execution import AIExecution
from app.models.design_version import DesignActivity
from app.models.event import Event
from app.models.presentation import PresentationCheckpoint
from app.services.ai_orchestration import ProviderResult, ai_execution_service
from conftest import test_engine
from test_assets import auth, upload_jpeg
from test_design_audit import _design_artifacts
from test_design_versioning import Versions, versions
from test_learning import ACTUAL, draft, reconciliation, verify, workflow
from test_phase9_spatial import create_object, create_scene, make_project
from test_presentation import concept, present, ref, sourced
from test_production import ProductionAPI, production, request
from test_production_procurement import sourcing
from test_project_memory_history import make_memory


@pytest.fixture(scope="module")
def migrated_database():
    schema = "phase11_" + uuid4().hex
    with test_engine.begin() as conn:
        conn.execute(CreateSchema(schema))
    engine = create_engine(test_engine.url, connect_args={"options": f"-csearch_path={schema}"})
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(database, "engine", engine)
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with test_engine.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))


@pytest.fixture
def db_session(migrated_database):
    with Session(migrated_database, autoflush=False) as db:
        yield db


def audit(versions, design, **options):
    return versions.call("POST", "/design-audits", 201, json={"design_version_id": design["id"], **options})


def learning(versions, path, method="GET", status=200, **kwargs):
    return versions.call(method, "/learning" + path, status, **kwargs)


def complete(versions):
    path = f"/api/v1/events/{versions.event.id}"
    request(versions.api.client, "PATCH", path, json={"status": "scheduled"})
    return request(versions.api.client, "PATCH", path, json={"status": "completed"})


def bind(versions, sheet, *, selection):
    pin = {"id": sheet["id"], "revision": sheet["revision"]}
    version = versions.save(selection={**selection, "presentation": pin})
    versions.call("PUT", f"/design-versions/{version['id']}/review", json=pin)
    return version, pin


def _cost(budget, amount, currency="INR", *, coverage="complete"):
    return {"baseline": {"kind": "budget", "id": budget["id"]}, "category": "budget_total",
            "amount": amount, "currency": currency, "coverage": coverage, "invoice_reference": "Finance closeout FT-43",
            "spent_on": "2026-10-10"}


@pytest.fixture
def approved(versions, present, concept, sourcing):
    dna, asset_id, sections = concept
    stage = create_object(versions.api.client, versions.api.scene_id, name="Edge decor", category="decor",
                          transform={"position": {"x": 29, "y": 5, "z": 1}})
    bom, material, *_ = sourcing.make_bom()
    budget = sourcing.call("POST", "/budgets", 201, params=versions.event_params, json={
        "expected_version": 0, "currency": "INR", "amount": "100", "bom_ids": [bom["id"]],
    })
    sourcing.call("POST", f"/budgets/{budget['id']}/approve", json={"expected_version": 1})
    scene = {"id": versions.api.scene_id}
    sheet = present.create(purpose="concept", sections={**sections, "budget_summary": sourced(ref("budget", budget)),
                                                       "spatial_plan": sourced(ref("spatial_scene", scene))})
    assert present.call("GET", f"/{sheet['id']}/validation")["complete"]
    version, pin = bind(versions, sheet, selection={"scene_ids": [scene["id"]], "budget_ids": [budget["id"]],
                                                     "bom_ids": [bom["id"]], "design_dna_id": str(dna.id)})
    checkpoint = present.checkpoint(sheet)
    present.review(sheet, checkpoint)
    decision = present.review(sheet, checkpoint, "decision", decision="approved", feedback="Client checkpoint signoff")
    assert versions.read(version)["status"] == "approved"
    return version, pin, sheet, decision, budget, bom, material, stage, asset_id


def test_approval_revision_and_end_to_end_history(versions, present, sourcing, approved, db_session):
    original, pin, sheet, decision, budget, bom, material, stage, asset = approved
    memory = make_memory(versions, "approved", verification="verified", linked_version_id=original["id"],
                         decision_id=decision["id"], source_reference="Signed checkpoint", content={"note": "Original concept approved"})
    reviewed = audit(versions, original)
    assert reviewed["snapshot_id"] == original["snapshot_id"] and len(reviewed["results"]) == 18
    key = reviewed["id"]
    layout = versions.call("GET", f"/design-audits/{key}/dimensions/scale_proportion")
    assert layout["score"] == 1 and any(target["id"] == stage["id"] for item in layout["recommendations"] for target in item["targets"])
    assert all(evidence in {item["id"] for item in layout["evidence"]} for item in layout["recommendations"] for evidence in item["evidence_ids"])
    safety = versions.call("GET", f"/design-audits/{key}/dimensions/risk_safety_flags")
    assert safety["score"] is None and safety["confidence"] == "inconclusive"

    activity, _ = workflow(versions)
    complete(versions)
    cost = verify(versions, draft(versions, "cost", original, audit_id=key, **_cost(budget, "93.50")))
    quantity = verify(versions, draft(versions, "quantity", original, bom_line_id=bom["lines"][0]["id"], material_id=material["id"],
                                     consumed={"value": "35", "unit": "m2"}))
    install = verify(versions, draft(versions, "installation", original, activity=activity, installed=ACTUAL))
    concept_actual = verify(versions, draft(versions, "concept_decision", original, checkpoint_id=decision["id"],
                                           memory_item_id=memory["id"], audit_id=key, source=None))
    verify(versions, draft(versions, "final_media", original, asset_id=asset, scene_id=versions.api.scene_id,
                           scene_object_id=stage["id"], description="Observed event image", source=None))
    incident = verify(versions, draft(versions, "production_incident", original, category="delivery", description="Site dispatch was late",
                                     occurred_at=ACTUAL["start"], reported_at=ACTUAL["end"], activities=[activity]))
    order = verify(versions, draft(versions, "change_order", original, original_scope="One trim", changed_scope="Two trims",
                                   requested_at=ACTUAL["start"], requester_reference="Site lead", approval_status="approved",
                                   approval_reference="CO-18", reason={"observation": "Signed additional scope", "source_reference": "CO-18"},
                                   reason_category="scope", activities=[activity], schedule_impact_minutes=15))
    report = reconciliation(versions, original, {"metric": "quantity", "bom_line_id": bom["lines"][0]["id"]})
    lookup = {item["metric"]: item for item in report["comparisons"]}
    assert float(lookup["cost"]["estimate"]) == 80 and float(lookup["cost"]["difference"]) == 13.5
    assert lookup["cost"]["percentage_difference"] == "16.8750"
    assert float(lookup["quantity"]["difference"]) == 3
    assert float(lookup["installation"]["difference"]) == 1800
    assert any(item["id"] == activity["task_id"] and item["revision"] == activity["revision"]
               for item in lookup["installation"]["sources"])
    assert any(item["id"] == bom["lines"][0]["id"] and item["revision"] == bom["version"]
               for item in lookup["quantity"]["sources"])
    assert any(item["id"] == budget["id"] and item["revision"] == budget["version"]
               for item in lookup["cost"]["sources"])
    corrected = verify(versions, draft(versions, "cost", original, supersedes=cost["id"], **_cost(budget, "93.50", "USD")))
    corrected_report = reconciliation(versions, original)
    assert next(row for row in corrected_report["comparisons"] if row["metric"] == "cost")["status"] == "currency_mismatch"

    second = Event(project_id=versions.project.id, organization_id=versions.owner["organization"].id, name="Previously completed event")
    db_session.add(second)
    db_session.commit()
    config = {"event_ids": [str(versions.event.id)], "reconciliation_ids": [report["id"]]}
    assert learning(versions, "/insights", "POST", 201, json=config)["insights"] == []
    other_scene = create_scene(versions.api.client, versions.project.id, event_id=str(second.id))
    other = Versions((ProductionAPI(versions.api.client, other_scene), versions.owner, versions.project, second))
    other_activity, _ = workflow(other)
    other_version = other.save(selection={"scene_ids": [other_scene["id"]]})
    complete(other)
    verify(other, draft(other, "installation", other_version, activity=other_activity, installed=ACTUAL))
    other_report = reconciliation(other, other_version)
    config = {"event_ids": [*config["event_ids"], str(second.id)], "reconciliation_ids": [corrected_report["id"], other_report["id"]],
              "idempotency_key": str(uuid4())}
    insights = learning(versions, "/insights", "POST", 201, json=config)
    assert learning(versions, "/insights", "POST", 201, json=config) == insights
    [item] = insights["insights"]
    assert item["sample_count"] == item["eligible_event_count"] == 2
    assert item["category"] == "installation" and item["period_start"] <= item["period_end"] and item["method"]
    evidence = learning(versions, f"/insights/{insights['id']}/evidence")
    assert {corrected_report["id"], other_report["id"], install["id"]} <= {row["id"] for row in evidence}
    disposition = f"/insights/{insights['id']}/items/{item['id']}/dispositions"
    pre_budget, pre_workflow = (sourcing.call("GET", f"/budgets/{budget['id']}"),
                               request(versions.api.client, "GET", versions.api.path + "/workflow"))
    receipt = learning(versions, disposition, "POST", 201, json={"decision": "accepted_for_review", "note": "Review next schedule"})
    assert learning(versions, disposition) == [receipt]
    assert sourcing.call("GET", f"/budgets/{budget['id']}") == pre_budget
    assert request(versions.api.client, "GET", versions.api.path + "/workflow") == pre_workflow
    assert versions.current()["id"] == original["id"]
    learning(versions, disposition.replace("/dispositions", "/apply"), "POST", 404, json={})

    # Re-read each persisted record after the event is complete and after revised outcomes exist.
    revised_sheet = present.revise(sheet, title="Later presentation material")
    assert revised_sheet["revision"] == pin["revision"] + 1 and revised_sheet["approval_status"] == "draft"
    project_path = f"/api/v1/projects/{versions.project.id}"
    request(versions.api.client, "PATCH", project_path, json={"status": "completed"})
    assert request(versions.api.client, "GET", project_path)["status"] == "completed"
    assert versions.call("PUT", f"/design-versions/{original['id']}/review", json=pin)["status"] == "approved"
    assert present.call("GET", f"/{sheet['id']}", params={"revision": pin["revision"]})["approval_status"] == "approved"
    assert present.call("GET", f"/{sheet['id']}/approvals", params={"revision": pin["revision"]}) == [decision]
    assert versions.read(original)["snapshot_id"] == original["snapshot_id"]
    assert versions.call("GET", f"/design-audits/{key}") == reviewed
    assert learning(versions, f"/reconciliations/{report['id']}") == report
    assert learning(versions, f"/insights/{insights['id']}") == insights
    assert request(versions.api.client, "GET", f"/api/v1/project-memory/items/{memory['id']}")["decision_id"] == decision["id"]
    [memory_revision] = request(versions.api.client, "GET", f"/api/v1/project-memory/items/{memory['id']}/history")
    assert memory_revision["state"]["linked_version_id"] == original["id"]
    assert memory_revision["state"]["decision_id"] == decision["id"]
    event = request(versions.api.client, "GET", f"/api/v1/events/{versions.event.id}")
    assert event["status"] == "completed"
    assert {cost["id"], corrected["id"], quantity["id"], incident["id"], order["id"], concept_actual["id"]} <= {
        row["id"] for row in learning(versions, "/actuals", params=versions.event_params)
    }
    assert {row["action"] for row in versions.call("GET", "/design-activity", params=versions.event_params)} >= {
        "presentation_revision_saved", "checkpoint_approved", "design_audit_completed_with_warnings",
        "memory_created", "learning_actual_verified", "learning_reconciliation_generated",
    }


def test_branch_audit_comparison_and_role_scope(versions, present, approved, identity_factory, db_session):
    original, pin, sheet, *_ = approved
    admin = identity_factory(role="admin", organization=versions.owner["organization"])
    editor = identity_factory(role="designer", organization=versions.owner["organization"])
    member = identity_factory(role="member", organization=versions.owner["organization"])
    outsider = identity_factory()
    alt_scene = create_scene(versions.api.client, versions.project.id, event_id=str(versions.event.id))
    create_object(versions.api.client, alt_scene["id"], transform={"position": {"x": 12, "y": 8, "z": 2}})
    main_audit = audit(versions, original)
    call = versions.call
    call("POST", "/design-audits", 403, headers=auth(member), json={"design_version_id": original["id"]})
    versions.api.client.headers.update(auth(editor))
    branch = versions.fork(original)
    changed = versions.save(branch["current_version_id"], selection={"scene_ids": [alt_scene["id"]]}, branch=branch)
    assert changed["based_on_id"] is None and changed["parent_id"] == branch["current_version_id"]
    versions.promote(branch, changed, original, 403)
    alternate = audit(versions, changed)
    params = {"from_evaluation_id": main_audit["id"], "to_evaluation_id": alternate["id"]}
    comparison = call("GET", "/design-audits/compare", params=params, headers=auth(member))
    dimensions = {row["dimension"]: row for row in comparison["dimensions"]}
    assert (dimensions["scale_proportion"]["from_score"], dimensions["scale_proportion"]["to_score"]) == (1, 3)
    assert dimensions["scale_proportion"]["comparable"]
    assert not dimensions["risk_safety_flags"]["comparable"]
    versions.api.client.headers.update(auth(admin))
    promoted = versions.promote(branch, changed, original)
    assert promoted["parent_id"] == original["id"] and promoted["based_on_id"] == changed["id"]
    restored = call("POST", "/design-versions/restore", 201, json={"source_version_id": original["id"], "expected_head_id": promoted["id"],
                     "summary": "Restore reviewed design", "reason": "Return to the client-approved plan"})
    assert restored["parent_id"] == promoted["id"] and restored["snapshot_id"] == original["snapshot_id"]
    assert restored["review"] is None and versions.read(original)["review"] == pin
    request(versions.api.client, "PATCH", f"/api/v1/projects/{versions.project.id}", json={
        "constraints": {"spatial_validation": {"collision": {"report_touching": True}}},
    })
    policy_version = versions.save(restored["id"], selection={"scene_ids": [alt_scene["id"]]}, refresh_context=True)
    policy_audit = audit(versions, policy_version)
    different_rules = call("GET", "/design-audits/compare", params={
        "from_evaluation_id": alternate["id"], "to_evaluation_id": policy_audit["id"],
    })
    scale = next(item for item in different_rules["dimensions"] if item["dimension"] == "scale_proportion")
    assert scale["from_score"] == scale["to_score"] == 3 and not scale["comparable"]
    assert call("GET", "/design-audits/" + main_audit["id"]) == main_audit
    call("GET", "/design-audits/" + main_audit["id"], 404, headers=auth(outsider))
    call("GET", "/design-versions/" + original["id"], 404, headers=auth(outsider))
    present.call("GET", "/" + sheet["id"], 404, headers=auth(outsider))
    foreign_project = make_project(db_session, outsider)
    foreign_event = Event(project_id=foreign_project.id, organization_id=outsider["organization"].id, name="Other tenant event")
    db_session.add(foreign_event)
    db_session.commit()
    foreign_image = upload_jpeg(versions.api.client, outsider, foreign_project.id)["asset_id"]
    request(versions.api.client, "GET", f"/api/v1/assets/{foreign_image}", 404)
    present.create(status=404, event_id=str(foreign_event.id))
    draft(versions, "final_media", status=404, description="Unrelated image", asset_id=foreign_image)
    learning(versions, "/insights", "POST", 404, json={"event_ids": [str(foreign_event.id)]})
    report = learning(versions, "/insights", "POST", 201, json={"event_ids": [str(versions.event.id)]})
    learning(versions, f"/insights/{report['id']}", status=404, headers=auth(outsider))


def test_database_rejects_scope_drift_and_historical_mutation(versions, present, approved, migrated_database, db_session):
    original, pin, sheet, decision, _, _, _, _, _ = approved
    reviewed = audit(versions, original)
    report = reconciliation(versions, original)
    actual = verify(versions, draft(versions, "production_incident", original, category="loading", description="Recorded event delay",
                                    occurred_at=ACTUAL["start"], reported_at=ACTUAL["end"]))
    latest = present.revise(sheet, title="Later candidate")
    pending = present.checkpoint(latest)
    present.review(latest, pending)
    second = Event(project_id=versions.project.id, organization_id=versions.owner["organization"].id, name="Unrelated event")
    db_session.add(second)
    db_session.commit()
    next_version = versions.save(original["id"])
    parameters = {
        "sheet": UUID(sheet["id"]), "decision": UUID(decision["id"]), "pending": UUID(pending["id"]),
        "version": UUID(next_version["id"]), "snapshot": UUID(original["snapshot_id"]), "audit": UUID(reviewed["id"]),
        "actual": UUID(actual["id"]), "report": UUID(report["id"]), "project": versions.project.id, "event": second.id,
    }
    violations = (
        "UPDATE presentations SET event_id = :event WHERE id = :sheet",
        "INSERT INTO presentations (id, project_id, event_id, alternative_of_id) VALUES (gen_random_uuid(), :project, :event, :sheet)",
        "UPDATE presentation_checkpoints SET revision = 1 WHERE id = :pending",
        "UPDATE presentation_checkpoints SET feedback = 'Overwrite' WHERE id = :decision",
        "UPDATE presentation_revisions SET sheet = '{}' WHERE presentation_id = :sheet AND version = 1",
        "INSERT INTO design_version_reviews (version_id, project_id, presentation_id, presentation_revision) VALUES (:version, :project, :sheet, 2)",
        "UPDATE design_snapshots SET state = '{}' WHERE id = :snapshot",
        "UPDATE design_audits SET warnings = jsonb_build_array('fixture_overwrite') WHERE id = :audit",
        "UPDATE learning_actuals SET data = '{}' WHERE id = :actual",
        "UPDATE learning_reports SET content = '{}' WHERE id = :report",
    )
    for statement in violations:
        with pytest.raises(IntegrityError) as error, migrated_database.begin() as conn:
            conn.execute(text(statement), parameters)
        assert error.value.orig.sqlstate == "23514"
    assert present.call("GET", f"/{sheet['id']}/approvals", params={"revision": pin["revision"]}) == [decision]
    assert versions.read(original)["status"] == "approved" and versions.read(next_version)["status"] == "draft"
    versions.call("PATCH", f"/design-versions/{original['id']}", 405, json={"reason": "Overwrite"})
    verify(versions, actual, 409)
    learning(versions, f"/reconciliations/{report['id']}", "PATCH", 405, json={})


def test_transaction_recovery_and_partial_completion(versions, present, concept, migrated_database, db_session, caplog):
    _, _, sections = concept
    sheet = present.create(purpose="concept", sections=sections)
    checkpoint = present.checkpoint(sheet)
    event_path = f"/api/v1/events/{versions.event.id}"
    rejected = request(versions.api.client, "DELETE", event_path, 409)
    assert rejected["detail"]["code"] == "presentation_history_retained"
    present.call("GET", f"/{sheet['id']}", 404, params={"revision": 999})
    version = versions.save()
    correlation = str(uuid4())
    try:
        with migrated_database.begin() as conn:
            conn.execute(text("""
                CREATE FUNCTION fixture_reject_activity() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION 'private fixture database detail' USING ERRCODE = '23514';
                END;
                $$
            """))
            conn.execute(text("""
                CREATE TRIGGER fixture_activity_failure BEFORE INSERT ON design_activities FOR EACH ROW
                WHEN (NEW.action IN ('checkpoint_pending', 'design_audit_started', 'learning_reconciliation_generated'))
                EXECUTE FUNCTION fixture_reject_activity()
            """))
        failed = present.call("POST", f"/{sheet['id']}/approvals/{checkpoint['id']}/request", 409,
                              json={"revision": 1}, headers={"X-Request-ID": correlation})
        assert failed["detail"]["code"] == "presentation_write_conflict"
        assert db_session.get(PresentationCheckpoint, UUID(checkpoint["id"])).status == "draft"
        result = versions.call("POST", "/design-audits", 409, headers={"X-Request-ID": correlation}, json={"design_version_id": version["id"]})
        assert result["detail"]["code"] == "audit_write_conflict"
        assert versions.call("GET", "/design-audits", params={"version_id": version["id"]}) == []
        expression = {"event_id": str(versions.event.id), "design_version_id": version["id"]}
        outcome = learning(versions, "/reconciliations", "POST", 409, json=expression)
        assert outcome["detail"]["code"] == "learning_relationship_conflict"
        assert learning(versions, "/reconciliations", params=versions.event_params) == []
        assert "private fixture database detail" not in str([failed, result, outcome, caplog.text])
    finally:
        with migrated_database.begin() as conn:
            conn.execute(text("DROP TRIGGER IF EXISTS fixture_activity_failure ON design_activities"))
            conn.execute(text("DROP FUNCTION IF EXISTS fixture_reject_activity()"))
    assert present.review(sheet, checkpoint)["status"] == "pending"
    assert audit(versions, version)["results"]
    saved = reconciliation(versions, version)
    assert reconciliation(versions, version)["id"] == saved["id"]


def test_review_roles_and_asset_withdrawal_between_request_and_decision(versions, present, concept, identity_factory):
    dna, asset, sections = concept
    request_id = str(uuid4())
    sheet = present.call("POST", status=201, json={"title": "Test exact approval", "client_context": "luxury",
                                                   "purpose": "concept", "event_id": str(versions.event.id), "sections": sections},
                         headers={"X-Request-ID": request_id})
    historical = versions.call("GET", "/design-activity", params=versions.event_params)
    assert any(item["request_id"] == request_id and item["entity_id"] == sheet["id"] for item in historical)
    design, pin = bind(versions, sheet, selection={"design_dna_id": str(dna.id)})
    designer = auth(identity_factory(role="designer", organization=versions.owner["organization"]))
    admin = auth(identity_factory(role="admin", organization=versions.owner["organization"]))
    member = auth(identity_factory(role="member", organization=versions.owner["organization"]))
    checkpoint = present.checkpoint(sheet)
    path = f"/{sheet['id']}/approvals/{checkpoint['id']}"
    present.call("POST", path + "/request", 403, json={"revision": 1}, headers=member)
    assert present.call("POST", path + "/request", json={"revision": 1}, headers=designer)["status"] == "pending"
    present.call("POST", path + "/decision", 403, json={"revision": 1, "decision": "approved"}, headers=designer)
    request(versions.api.client, "DELETE", f"/api/v1/assets/{asset}")
    failed = present.call("POST", path + "/decision", 409, json={"revision": 1, "decision": "approved"}, headers=admin)
    assert failed["detail"]["code"] == "presentation_incomplete"
    assert versions.read(design)["status"] == "pending"
    report = audit(versions, design)
    assert any(warning["code"] == "asset_evidence_unavailable" for warning in report["warnings"])
    assert asset in versions.read(design)["unavailable_asset_ids"]
    assert asset not in versions.read(design)["state"]["assets"]
    request(versions.api.client, "POST", f"/api/v1/assets/{asset}/restore")
    assert present.call("POST", path + "/decision", json={"revision": pin["revision"], "decision": "approved"}, headers=admin)["status"] == "approved"


def test_provider_timeout_and_invalid_output_preserve_measurements(versions, db_session, identity_factory, monkeypatch, caplog):
    import json
    import httpx

    from app.services.ai_orchestration import OpenAIResponsesAdapter

    mode = {"result": "timeout", "calls": 0}
    secret = "phase-11-dummy-provider-credential"
    caplog.set_level(logging.INFO, logger="insho")

    def response(req):
        mode["calls"] += 1
        payload = json.loads(req.content)
        assert set(json.loads(payload["input"])) == {"creativity", "photography_social_impact"}
        if mode["result"] == "timeout":
            raise httpx.ReadTimeout("Synthetic provider text with " + secret, request=req)
        if mode["result"] == "unavailable":
            return httpx.Response(503, json={"error": {"message": secret}}, request=req)
        return httpx.Response(200, json={"output_text": '{"assessments":[{"dimension":"unknown","assessment":"supported"}]}'}, request=req)

    adapter = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: adapter(transport=httpx.MockTransport(response), **kwargs))
    monkeypatch.setitem(ai_execution_service.adapters, "openai", OpenAIResponsesAdapter())
    for key, value in {"AI_OPENAI_ENABLED": "true", "OPENAI_API_KEY": secret, "AI_OPENAI_MODELS": "fixture",
                       "AI_OPENAI_CAPABILITIES": "text,structured_output", "AI_ANTHROPIC_ENABLED": "false", "AI_MAX_RETRIES": "0"}.items():
        monkeypatch.setenv(key, value)
    create_object(versions.api.client, versions.api.scene_id, transform={"position": {"x": 29, "y": 5, "z": 1}})
    selected = versions.save(selection={"scene_ids": [versions.api.scene_id], "artifact_ids": _design_artifacts(versions, db_session)})
    outsider = auth(identity_factory())
    versions.call("POST", "/design-audits", 404, headers=outsider, json={"design_version_id": selected["id"], "ai_assisted": True})
    assert mode["calls"] == 0
    first = audit(versions, selected, ai_assisted=True)
    assert first["retryable"] and first["warnings"][0]["code"] == "ai_review_unavailable"
    assert db_session.get(AIExecution, UUID(first["ai_execution_id"])).error_code == "provider_timeout"
    dimensions = {item["dimension"]: item for item in first["results"]}
    assert dimensions["scale_proportion"]["score"] == 1 and dimensions["creativity"]["score"] is None
    mode["result"] = "unavailable"
    offline = versions.call("POST", f"/design-audits/{first['id']}/retry", 201)
    assert offline["retry_of_id"] == first["id"] and offline["retryable"]
    assert db_session.get(AIExecution, UUID(offline["ai_execution_id"])).error_code == "provider_unavailable"
    mode["result"] = "invalid"
    retried = versions.call("POST", f"/design-audits/{offline['id']}/retry", 201)
    assert retried["retry_of_id"] == offline["id"] and retried["warnings"][0]["code"] == "ai_output_invalid"
    assert {item["dimension"]: item for item in retried["results"]}["scale_proportion"] == dimensions["scale_proportion"]
    assert versions.call("GET", f"/design-audits/{first['id']}") == first
    assert mode["calls"] == 3 and secret not in str([first, offline, retried, caplog.text])
    insho_records = [vars(record) for record in caplog.records if record.name.startswith("insho.")]
    assert insho_records and secret not in str(insho_records)


def test_seeded_migration_round_trip_and_failed_upgrade_are_atomic(versions, approved, sourcing, migrated_database, db_session):
    original, pin, sheet, decision, budget, bom, _, _, _ = approved
    selected = audit(versions, original)
    actual = verify(versions, draft(versions, "cost", original, audit_id=selected["id"], **_cost(budget, "90")))
    report = reconciliation(versions, original)
    memory = make_memory(versions, "approved", verification="verified", linked_version_id=original["id"],
                         decision_id=decision["id"], source_reference="Signed review")
    another_project = make_project(db_session, versions.owner)
    bad_sheet = uuid4()
    bind_params = {"id": bad_sheet, "project": another_project.id, "event": versions.event.id}
    db_session.rollback()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(database, "engine", migrated_database)
        command.downgrade(config, "d914b27f803c")
        with migrated_database.begin() as conn:
            conn.execute(text("INSERT INTO presentations (id, project_id, event_id) VALUES (:id, :project, :event)"), bind_params)
        with pytest.raises(IntegrityError):
            command.upgrade(config, "head")
        with migrated_database.begin() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "d914b27f803c"
            assert conn.scalar(text("SELECT count(*) FROM presentations WHERE id = :id"), bind_params) == 1
            conn.execute(text("DELETE FROM presentations WHERE id = :id"), bind_params)
        command.upgrade(config, "head")
        command.check(config)

    assert sourcing.call("GET", "/budgets/" + budget["id"])["version"] == budget["version"]
    assert sourcing.parts.get(bom)["costs"] == bom["costs"]
    assert versions.call("GET", f"/presentations/{sheet['id']}")["approval_status"] == "approved"
    assert versions.call("GET", f"/presentations/{sheet['id']}/approvals") == [decision]
    assert versions.read(original)["state"] == original["state"]
    assert request(versions.api.client, "GET", f"/api/v1/project-memory/items/{memory['id']}") == memory
    assert versions.call("GET", f"/design-audits/{selected['id']}") == selected
    assert learning(versions, f"/actuals/{actual['id']}") == actual
    assert learning(versions, f"/reconciliations/{report['id']}") == report


def test_learning_report_and_provider_share_request_correlation(versions, db_session, monkeypatch):
    import json

    from app.models.production_procurement import ProductionProjectSupplier, ProductionSupplier

    supplier = ProductionSupplier(organization_id=versions.owner["organization"].id, name="Fixture supplier", status="active",
                                  version=1, details={})
    second = Event(organization_id=versions.owner["organization"].id, project_id=versions.project.id, name="Other event")
    db_session.add_all([supplier, second])
    db_session.flush()
    db_session.add(ProductionProjectSupplier(project_id=versions.project.id, supplier_id=supplier.id, version=1, active=True, history=[]))
    db_session.commit()
    for key in [versions.event.id, second.id]:
        verify(versions, draft(versions, "vendor", event=key, supplier_id=str(supplier.id), period=ACTUAL, dimension="delivery_timing",
                               outcome="missed", observation="Fixture confidential shipping note"))

    class Review:
        async def execute(self, model, request, timeout):
            [item] = json.loads(request.input)
            assert set(item) == {"insight_id", "category", "eligible_event_count", "sample_count", "evidence_ids"}
            return ProviderResult(output=json.dumps({"reviews": [{"insight_id": item["insight_id"],
                                   "evidence_id": item["evidence_ids"][0], "emphasis": "review_execution"}]}))

    monkeypatch.setitem(ai_execution_service.adapters, "openai", Review())
    for key, value in {"AI_OPENAI_ENABLED": "true", "OPENAI_API_KEY": "fixture", "AI_OPENAI_MODELS": "fixture",
                       "AI_OPENAI_CAPABILITIES": "text,structured_output", "AI_ANTHROPIC_ENABLED": "false", "AI_MAX_RETRIES": "0"}.items():
        monkeypatch.setenv(key, value)
    report = learning(versions, "/insights", "POST", 201,
                      json={"event_ids": [str(versions.event.id), str(second.id)], "ai_assisted": True})
    execution = db_session.get(AIExecution, UUID(report["ai_execution_id"]))
    activity = db_session.scalar(select(DesignActivity).where(DesignActivity.entity_id == UUID(report["id"])))
    assert activity.request_id == execution.request_id
    assert activity.actor_id == versions.owner["user"].id and activity.execution_id == execution.id
    assert report["insights"][0]["ai_origin"] == "ai_inferred"


def test_local_learning_load_and_bounded_query_count(versions, concept, approved, migrated_database, db_session):
    import re
    from time import perf_counter

    from sqlalchemy import event, insert

    from app.models.learning import LearningActual
    from app.models.production_procurement import ProductionProjectSupplier, ProductionSupplier

    _, asset, _ = concept
    read_model = verify(versions, draft(versions, "final_media", asset_id=asset, source=None, description="Captured event media"))
    template = db_session.get(LearningActual, UUID(read_model["id"]))
    scope = {"organization_id": template.organization_id, "project_id": template.project_id, "origin": "reported",
             "recorded_by_id": template.recorded_by_id, "verified_by_id": template.verified_by_id, "status": "verified",
             "revision": template.revision, "verified_at": template.verified_at, "occurred_on": template.occurred_on,
             "review_note": template.review_note}
    db_session.execute(insert(LearningActual), [{**scope, "id": uuid4(), "event_id": versions.event.id, "kind": "final_media",
                                               "data": template.data, "derived": template.derived, "sources": template.sources}
                                              for _ in range(99)])
    # Use existing project supplier attribution and the actual write path for the first observation.
    supplier = ProductionSupplier(organization_id=template.organization_id, name="Volume fixture supplier", status="active",
                                  version=1, details={})
    events = [Event(organization_id=template.organization_id, project_id=template.project_id, name=f"Volume event {n}")
              for n in range(20)]
    db_session.add(supplier)
    db_session.add_all(events)
    db_session.flush()
    db_session.add(ProductionProjectSupplier(project_id=template.project_id, supplier_id=supplier.id, version=1, active=True, history=[]))
    db_session.commit()
    observed = verify(versions, draft(versions, "vendor", event=events[0].id, supplier_id=str(supplier.id), period=ACTUAL,
                                      dimension="delivery_reliability", outcome="missed", observation="Source-attributed miss"))
    observed_row = db_session.get(LearningActual, UUID(observed["id"]))
    rows = [{**scope, "id": uuid4(), "event_id": entry.id, "kind": "vendor", "data": observed_row.data,
             "derived": observed_row.derived, "sources": observed_row.sources}
            for index, entry in enumerate(events) for _ in range(24 if index == 0 else 25)]
    db_session.execute(insert(LearningActual), rows)
    db_session.commit()
    event_ids = [str(entry.id) for entry in events]
    version, _, _, _, budget, *_ = approved
    cost = verify(versions, draft(versions, "cost", version, **_cost(budget, "90")))
    cost_row = db_session.get(LearningActual, UUID(cost["id"]))
    partial = {**scope, "event_id": versions.event.id, "design_version_id": UUID(version["id"]),
               "snapshot_id": UUID(version["snapshot_id"]), "kind": "cost", "data": {**cost_row.data, "coverage": "partial"},
               "derived": cost_row.derived, "sources": cost_row.sources}
    db_session.execute(insert(LearningActual), [{**partial, "id": uuid4()} for _ in range(499)])
    db_session.commit()

    statements = []

    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(migrated_database, "before_cursor_execute", count)
    try:
        began = perf_counter()
        page = learning(versions, "/actuals", params={**versions.event_params, "kind": "final_media", "limit": 100})
        media_ms = (perf_counter() - began) * 1000
        asset_reads = sum(re.search(r"\bFROM\s+assets\b", item, re.I) is not None for item in statements)
        actual_queries = len(statements)
        assert len(page) == 100 and asset_reads == 1
        statements.clear()
        began = perf_counter()
        run = learning(versions, "/insights", "POST", 201, json={"event_ids": event_ids})
        insight_ms = (perf_counter() - began) * 1000
        insight_queries = len(statements)
        assert insight_queries <= 24
        statements.clear()
        began = perf_counter()
        result = reconciliation(versions, version)
        reconcile_ms = (perf_counter() - began) * 1000
        reconcile_queries = len(statements)
        assert reconcile_queries <= 24
    finally:
        event.remove(migrated_database, "before_cursor_execute", count)
    [item] = run["insights"]
    assert item["sample_count"] == 20 and item["confidence"] == "moderate"
    page = learning(versions, f"/insights/{run['id']}/evidence", params={"offset": 450, "limit": 100})
    assert 50 <= len(page) <= 100
    [comparison] = result["comparisons"]
    assert len(comparison["actual_ids"]) == 500 and comparison["verified_actual_id"] == cost["id"]
    db_session.execute(insert(LearningActual), [{**partial, "id": uuid4()}])
    db_session.commit()
    learning(versions, "/reconciliations", "POST", 422,
             json={"event_id": str(versions.event.id), "design_version_id": version["id"]})
    print(f"Phase 11 local workload: 100 actuals in {media_ms:.1f} ms, {actual_queries} SQL statements, {asset_reads} asset SELECT; "
          f"500 observations across 20 events in {insight_ms:.1f} ms, {insight_queries} SQL statements; "
          f"500 reconciliation inputs in {reconcile_ms:.1f} ms, {reconcile_queries} SQL statements.")
