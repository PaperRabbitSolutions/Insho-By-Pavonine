from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from app.models.design_artifact import DesignArtifact
from app.services import procurement_scope
from app.services.production_costing import compare_quantities
from app.services.production_workflow import critical_path
from test_phase9_spatial import create_object, create_scene, make_project
from test_production import ProductionAPI, production, request
from test_production_bom import generate, manual
from test_production_procurement import day, sourcing
from test_production_quantification import calculate, patch_object, quantity_request, revision


def technical_sheet(api, item, quantity, *, previous=None, status=201):
    path = f"/items/{item}/specifications"
    result = api.write("POST", path, status, name="Stage deck fabrication", content="Use the approved shop detail.",
                       supersedes_id=previous, technical={"source": "spatial_geometry", "target": "object",
                       "scene_revision": revision(api), "finish": "Matte", "quantity_ids": [quantity["id"]]})
    if status == 201:
        api.write("POST", path + f"/{result['id']}/approve", scene_revision=revision(api))
        return path + "/" + result["id"], result["id"]
    return result


def transition(api, workflow, status, expected=200, **fields):
    return request(api.client, "POST", api.path + "/workflow/status", expected, json={
        "expected_version": workflow["version"], "status": status, **fields,
    })


def approve_alternative(sourcing, purchase, quote, **references):
    path = f"/procurements/{purchase['id']}/proposals"
    proposal = sourcing.call("POST", path, 201, json={
        "expected_procurement_version": purchase["version"], "quotation_id": quote["id"],
        "rationale": "Reduce deck cost", "specification_comparison": "Compare signed shop drawings",
        "evidence_reference": "Signed shop drawing SR-1", "quantity_impact": "Same measured supply",
        "visual_impact": "Sample reviewed", "technical_impact": "See reviewed shop details",
        "production_impact": "Reuse installation sequence", "lead_time_impact": "Same delivery window",
        "risks": "Check the sample on delivery", **references,
    })
    decision_path = path + f"/{proposal['id']}/decision"
    approved = sourcing.call("POST", decision_path, json={
        "expected_version": proposal["version"], "expected_procurement_version": purchase["version"], "decision": "approved",
    })
    return approved, decision_path


def test_stage_change_from_spatial_object_through_approved_sourcing(sourcing, monkeypatch):
    api, parts = sourcing.api, sourcing.parts
    event_params = {"event_id": str(sourcing.event.id)}
    stage = create_object(api.client, api.scene_id)
    assembly = api.item("Stage", kind="assembly", spatial_object_id=stage["id"])
    definition = parts.material()
    material = api.write("POST", "/materials", 201, name="Deck panels", definition_id=definition["id"])["id"]
    deck = api.item("Deck panels", parent_item_id=assembly, material_id=material, spatial_object_id=stage["id"])
    area, count = calculate(api, quantity_request(assembly, "plan_area"), quantity_request(assembly, "object_count"))
    first_sheet_path, first_sheet_id = technical_sheet(api, deck, area)
    first_sheet = request(api.client, "GET", api.path + first_sheet_path)
    assert first_sheet["technical"]["quantity_refs"] == [{"id": area["id"], "version": 1}]
    assert first_sheet["technical"]["source"]["target_id"] == stage["id"]
    assert first_sheet["technical"]["source"]["captured_by_user_id"] == str(sourcing.owner["user"].id)
    wrong_bom = parts.revise(generate(count, item=deck, spec=first_sheet_id))
    assert "specification_quantity_mismatch" in {issue["code"] for issue in wrong_bom["lines"][0]["issues"]}
    parts.approve(wrong_bom, status=409)
    bom = parts.approve(parts.revise(
        {"type": "remove", "line_id": wrong_bom["lines"][0]["id"]}, generate(area, item=deck, spec=first_sheet_id),
    ))
    line = bom["lines"][0]
    assert line["total"] == "80.00"
    assert line["inputs"]["dependencies"]["component"]["parent_ids"] == [assembly]
    assert line["inputs"]["dependencies"]["material"]["id"] == material
    assert line["inputs"]["dependencies"]["quantity"]["id"] == area["id"]

    supplier = sourcing.supplier()
    quote = sourcing.quote_decision(sourcing.quote(bom, supplier, delivery_charge="4"))
    comparison = sourcing.call("GET", f"/boms/{bom['id']}/quote-comparison")
    assert comparison["quotes"][0]["lines"][0]["comparison"]["price_difference"] == "-16.00"
    assert [row["id"] for row in sourcing.call("GET", "/quotes", params={**event_params, "status": "approved"})] == [quote["id"]]
    purchase = sourcing.procurement(bom)[0]
    assert sourcing.procurement(bom)[0]["id"] == purchase["id"]
    assert purchase["bom_line_id"] == line["id"]
    alternative = parts.material(name="Alternative stage panel", specification="Alternative panel sample")
    alternate_quote = sourcing.quote_decision(sourcing.quote(bom, supplier, reference="Q-ALT", delivery_charge="4", lines=[
        sourcing.line(bom, material_id=alternative["id"], unit_rate="1.5", specification="Alternative panel sample"),
    ]))
    budget_input = {"expected_version": 0, "currency": "INR", "amount": "100", "allocations": {"material": "100"},
                    "bom_ids": [bom["id"]], "quote_ids": [alternate_quote["id"]]}
    pending_budget = sourcing.call("POST", "/budgets", 201, params=event_params, json=budget_input)
    assert pending_budget["summary"]["quoted"]["total"] is None
    proposal, proposal_decision = approve_alternative(sourcing, purchase, alternate_quote)
    assert proposal["proposal"]["original"]["material_id"] == definition["id"]
    assert proposal["proposal"]["alternative"]["material"]["id"] == alternative["id"]
    applied = sourcing.call("POST", proposal_decision, json={
        "expected_version": proposal["version"], "expected_procurement_version": purchase["version"], "decision": "applied",
    })
    purchase = sourcing.status(sourcing.call("GET", f"/procurements/{purchase['id']}"), "approved")
    assert purchase["approval"]["approved_cost"] == "52.00"
    assert sourcing.call("GET", f"/budgets/{pending_budget['id']}")["stale_reasons"] == ["quotation_requirements_changed"]
    budget_input["expected_version"] = 1
    budget = sourcing.call("POST", "/budgets", 201, params=event_params, json=budget_input)
    budget_path = f"/budgets/{budget['id']}"
    budget = sourcing.call("POST", budget_path + "/approve", json={"expected_version": 2})
    assert budget["summary"]["quoted_variance"] == "-48.00" and budget["summary"]["actual"]["total"] is None
    assert budget["sources"]["quotes"][0]["requirement_reviews"][line["id"]]["substitution"]["proposal_id"] == applied["id"]
    request(api.client, "DELETE", f"/api/v1/events/{sourcing.event.id}", 409)
    request(api.client, "DELETE", f"/api/v1/projects/{sourcing.project.id}", 409)

    with monkeypatch.context() as now:
        now.setattr(procurement_scope, "today", lambda: date.fromisoformat(day(11)))
        assert sourcing.call("GET", budget_path)["stale_reasons"] == ["quotation_changed"]
        assert sourcing.call("GET", f"/procurements/{purchase['id']}")["stale_reasons"] == ["quotation_expired"]

    workflow = request(api.client, "GET", api.path + "/workflow")
    workflow = transition(api, workflow, "design_approved", evidence_reference="Client signoff SD-12")
    workflow = transition(api, workflow, "technical_review")
    ready = transition(api, workflow, "production_ready")
    assert ready["readiness_basis"]["bom_id"] == bom["id"]
    assert ready["readiness_basis"]["specifications"] == [{"id": first_sheet_id, "version": 1, "item_id": deck}]

    patch_object(api, stage, dimensions={"width_m": 6, "depth_m": 4, "height_m": 1})
    quantity_path = api.path + f"/quantities/{area['id']}"
    assert request(api.client, "GET", quantity_path)["stale"]
    assert "quantity_changed" in request(api.client, "GET", api.path + first_sheet_path)["stale_reasons"]
    assert set(parts.get(bom)["lines"][0]["stale_reasons"]) == {"quantity_changed", "specification_changed"}
    assert sourcing.call("GET", f"/quotes/{quote['id']}")["stale_reasons"] == ["bom_changed"]
    assert sourcing.call("GET", proposal_decision.removesuffix("/decision"))["stale"]
    assert "bom_changed" in sourcing.call("GET", budget_path)["stale_reasons"]
    assert sourcing.call("GET", f"/procurements/{purchase['id']}")["approval"] == purchase["approval"]
    assert request(api.client, "GET", api.path + "/workflow")["readiness_stale"]
    transition(api, ready, "in_fabrication", expected=409)
    review = transition(api, ready, "technical_review")

    changed = api.write("POST", f"/quantities/{area['id']}/recalculate", scene_revision=revision(api))["quantities"][0]
    technical_sheet(api, deck, area, previous=first_sheet_id, status=409)
    _, second_sheet_id = technical_sheet(api, deck, changed, previous=first_sheet_id)
    assert changed["value"] == "24.000000" and changed["version"] == 2
    revised = parts.approve(parts.revise(generate(changed, item=deck, spec=second_sheet_id)))
    assert revised["lines"][0]["line_key"] == line["line_key"] and revised["lines"][0]["total"] == "60.00"
    new_quote = sourcing.quote_decision(sourcing.quote(revised, supplier, expected_version=1, delivery_charge="2",
                                                     lines=[sourcing.line(revised, quantity="24")]))
    new_budget = sourcing.call("POST", "/budgets", 201, params=event_params, json={
        **budget_input, "expected_version": 2, "bom_ids": [revised["id"]], "quote_ids": [new_quote["id"]],
    })
    assert not new_budget["stale"] and new_budget["approved_at"] is None
    assert new_budget["summary"]["estimated"]["total"] == "60.00" and new_budget["summary"]["quoted"]["total"] == "50.00"
    assert sourcing.call("GET", budget_path)["summary"] == budget["summary"]
    historical_bom = parts.get(bom)
    assert datetime.fromisoformat(historical_bom["approved_at"].replace("Z", "+00:00")) == datetime.fromisoformat(
        bom["approved_at"].replace("Z", "+00:00")
    )
    assert historical_bom["lines"][0]["total"] == "80.00"
    assert request(api.client, "GET", quantity_path)["value"] == "32.000000"
    assert request(api.client, "GET", api.path + first_sheet_path)["approved_at"] == first_sheet["approved_at"]
    assert transition(api, review, "production_ready")["readiness_basis"]["bom_id"] == revised["id"]


def test_production_lifecycle_plan_transitions_and_permissions(sourcing, identity_factory):
    api, owner = sourcing.api, sourcing.owner
    workflow_path = api.path + "/workflow"
    current = request(api.client, "GET", workflow_path)
    assert (current["status"], current["version"], current["allowed_transitions"]) == ("draft", 0, ["design_approved"])
    transition(api, current, "production_ready", expected=409)
    transition(api, current, "design_approved", expected=422)
    supplier = sourcing.supplier()
    fabrication_id, install_id = str(uuid4()), str(uuid4())
    tasks = [
        {"id": fabrication_id, "name": "Cut deck", "kind": "fabrication", "duration_minutes": 40,
         "supplier_id": supplier["id"]},
        {"id": install_id, "name": "Set deck", "kind": "installation", "duration_minutes": 30,
         "depends_on": [fabrication_id], "window_start": "2027-01-05T14:00:00+05:30", "window_end": "2027-01-05T16:00:00+05:30"},
    ]
    plan_path = workflow_path + "/plan"
    request(api.client, "PUT", plan_path, 422, json={"expected_version": 0, "tasks": tasks[::-1]})
    request(api.client, "PUT", plan_path, 422, json={"expected_version": 0, "tasks": tasks, "status": "live"})
    request(api.client, "GET", workflow_path, 401, headers={"Authorization": ""})
    for role, organization, code in [("member", owner["organization"], 403), ("owner", None, 404)]:
        identity = identity_factory(role=role, organization=organization)
        headers = {"Authorization": "Bearer " + identity["token"]}
        request(api.client, "GET", workflow_path, 200 if role == "member" else 404, headers=headers)
        request(api.client, "PUT", plan_path, code, headers=headers, json={"expected_version": 0, "tasks": tasks})
    designer = identity_factory(role="designer", organization=owner["organization"])
    api.client.headers["Authorization"] = "Bearer " + designer["token"]
    current = request(api.client, "PUT", plan_path, json={"expected_version": 0, "tasks": tasks})
    assert current["critical_path"] == [fabrication_id, install_id] and current["critical_path_minutes"] == 70
    transition(api, current, "design_approved", expected=403, evidence_reference="Review")
    api.client.headers["Authorization"] = "Bearer " + owner["token"]
    request(api.client, "PUT", plan_path, 409, json={"expected_version": 0, "tasks": tasks})
    api.delete("", status=409)
    current = transition(api, current, "design_approved", evidence_reference="Client signature AD-1")
    current = transition(api, current, "technical_review")
    problem = transition(api, current, "production_ready", expected=409)
    assert problem["detail"]["code"] == "production_package_unready"
    assert {row["code"] for row in problem["detail"]["blockers"]} == {"approved_bom_required", "boq_requirements_missing"}

    obj = create_object(api.client, api.scene_id)
    item = api.item(spatial_object_id=obj["id"])
    quantity = calculate(api, quantity_request(item, "plan_area"))[0]
    technical_sheet(api, item, quantity)
    bom = sourcing.parts.revise(manual(item_id=item))
    sourcing.parts.approve(bom)
    current = transition(api, current, "production_ready")
    request(api.client, "PUT", plan_path, 409, json={"expected_version": current["version"], "tasks": []})
    for status in ("in_fabrication", "on_site", "installed", "inspected", "live", "dismantling", "archived"):
        current = transition(api, current, status, evidence_reference="Site record 105")
    assert current["status"] == "archived" and current["allowed_transitions"] == []
    assert current["readiness_basis"]["bom_id"] == bom["id"]
    history = request(api.client, "GET", workflow_path + "/history", params={"limit": 2, "offset": 1})
    assert [row["status"] for row in history] == ["dismantling", "live"]
    assert all(row["changed_by_user_id"] == str(owner["user"].id) for row in history)
    assert api.get()["status"] == "draft"


def test_replacement_record_and_quantity_reference_scope(sourcing, db_session, identity_factory):
    api, owner = sourcing.api, sourcing.owner
    stage = create_object(api.client, api.scene_id)
    item = api.item(spatial_object_id=stage["id"])
    sibling = api.item(spatial_object_id=stage["id"])
    area = calculate(api, quantity_request(item, "plan_area"))[0]
    technical_sheet(api, sibling, area, status=404)
    request(api.client, "POST", api.base, 409, json={"name": "New plan", "supersedes_id": api.record["id"]})
    api.sources(revision(api))
    approved = api.sources(revision(api), action="approve")
    old = api.get()
    patch_object(api, stage, dimensions={"width_m": 6, "depth_m": 4, "height_m": 1})
    new = request(api.client, "POST", api.base, 201, json={"name": "New plan", "supersedes_id": approved["id"]})
    assert new["supersedes_id"] == approved["id"] and new["status"] == "draft"
    new_path = api.base + "/" + new["id"]
    assert request(api.client, "GET", new_path)["items"] == []
    assert api.get()["approved_at"] == old["approved_at"] and api.get()["version"] == old["version"]
    external = identity_factory(role="owner")
    project = make_project(db_session, external)
    api.client.headers["Authorization"] = "Bearer " + external["token"]
    foreign = ProductionAPI(api.client, create_scene(api.client, project.id))
    request(api.client, "POST", foreign.base, 404, json={"name": "New plan", "supersedes_id": approved["id"]})
    api.client.headers["Authorization"] = "Bearer " + owner["token"]


def test_mixed_units_compare_before_rounding_and_critical_path_uses_dependencies():
    assert compare_quantities(Decimal(144), "in2", Decimal(1), "ft2") == 0
    assert compare_quantities(Decimal("999999.6"), "mm2", Decimal(1), "m2") == -1
    assert compare_quantities(Decimal("1000000.4"), "mm2", Decimal(1), "m2") == 1
    tasks = [
        {"id": "cut", "duration_minutes": 20, "depends_on": []},
        {"id": "wire", "duration_minutes": 35, "depends_on": []},
        {"id": "test", "duration_minutes": 10, "depends_on": ["cut", "wire"]},
    ]
    assert critical_path(tasks) == (["wire", "test"], 45)
    assert critical_path([*tasks, {"id": "install", "duration_minutes": None, "depends_on": ["test"]}]) == (None, None)


def test_stale_substitution_requires_explicit_replacement_before_procurement(sourcing, db_session):
    bom, *_ = sourcing.make_bom()
    original_line = bom["lines"][0]
    material = sourcing.parts.material(name="Alternate", specification="Review sample")
    supplier = sourcing.supplier()
    alternative = sourcing.line(bom, material_id=material["id"], specification="Review sample", unit_rate="1.50")
    quote = sourcing.quote_decision(sourcing.quote(bom, supplier, lines=[alternative]))
    purchase = sourcing.procurement(bom)[0]
    purchase_path = f"/procurements/{purchase['id']}"
    artifact = DesignArtifact(project_id=sourcing.project.id, event_id=sourcing.event.id, module="style", version=1,
                              event_scope_key=str(sourcing.event.id), status="approved", content={}, source="user_edited",
                              context_fingerprint="fixture", created_by_user_id=sourcing.owner["user"].id)
    db_session.add(artifact)
    db_session.commit()
    proposal, decision = approve_alternative(sourcing, purchase, quote, design_artifact_id=str(artifact.id))
    apply_body = {"expected_version": proposal["version"], "expected_procurement_version": purchase["version"],
                  "decision": "applied"}
    sourcing.call("POST", decision, json=apply_body)
    budget = sourcing.call("POST", "/budgets", 201, json={
        "expected_version": 0, "currency": "INR", "amount": "100", "bom_ids": [bom["id"]], "quote_ids": [quote["id"]],
    })
    assert budget["summary"]["quoted"]["total"] == "48.00"
    artifact.status = "archived"
    db_session.commit()
    current = sourcing.call("GET", purchase_path)
    assert current["stale_reasons"] == ["substitution_changed"]
    assert sourcing.status(current, "approved", 409)["detail"]["code"] == "procurement_substitution_stale"
    assert sourcing.call("GET", f"/budgets/{budget['id']}")["stale_reasons"] == ["quotation_requirements_changed"]
    second, second_decision = approve_alternative(sourcing, current, quote)
    replacement_body = {"expected_version": second["version"], "expected_procurement_version": current["version"],
                        "decision": "applied"}
    sourcing.call("POST", second_decision, 409, json=replacement_body)
    sourcing.call("POST", second_decision, json={**replacement_body, "replaces_proposal_id": proposal["id"]})
    replaced = sourcing.call("GET", decision.removesuffix("/decision"))
    assert replaced["status"] == "applied" and "application_replaced" in replaced["stale_reasons"]
    approved = sourcing.status(sourcing.call("GET", purchase_path), "approved")
    assert approved["approval"]["substitution"]["proposal_id"] == second["id"]
    assert approved["requirement"]["bom_line_id"] == original_line["id"]


def test_event_currency_change_invalidates_bom_costs_and_commercial_decisions(sourcing):
    bom, *_ = sourcing.make_bom(approved=False)
    quote = sourcing.quote(bom, sourcing.supplier())
    request(sourcing.api.client, "PATCH", f"/api/v1/events/{sourcing.event.id}", json={"currency": "USD"})
    history = sourcing.parts.get(bom)
    assert history["lines"][0]["stale_reasons"] == ["event_currency_changed"]
    assert history["lines"][0]["total"] == "80.00"
    summary = sourcing.call("GET", "/cost-summary", params={"event_id": str(sourcing.event.id)})
    assert summary["records"][0]["stale"] and summary["totals"][0]["total"] is None
    sourcing.parts.approve(bom, status=409)
    sourcing.quote_decision(quote, status=409)


def test_approved_project_budget_protects_its_draft_bom_from_cascading_deletion(sourcing):
    api = sourcing.api
    bom = sourcing.parts.revise(manual())
    budget = sourcing.call("POST", "/budgets", 201, json={
        "expected_version": 0, "currency": "INR", "amount": "100", "bom_ids": [bom["id"]],
    })
    sourcing.call("POST", f"/budgets/{budget['id']}/approve", json={"expected_version": 1})
    failure = request(api.client, "DELETE", api.path, 409, params={"expected_version": api.version})
    assert failure["detail"]["code"] == "approved_budget_history"
    event_delete = request(api.client, "DELETE", f"/api/v1/events/{sourcing.event.id}", 409)
    assert event_delete["detail"]["code"] == "approved_budget_history"
    assert sourcing.parts.get(bom)["lines"][0]["total"] == "45.31"
