from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.asset import Asset
from app.models.design_artifact import DesignArtifact
from app.models.event import Event
from app.models.production_procurement import ProductionProcurement
from app.services.asset_lifecycle import cleanup_deleted_assets
from test_assets import upload_jpeg
from test_phase9_spatial import make_project
from test_production import production, request
from test_production_bom import BOMAPI, generate, manual, mapped_takeoff


def day(offset=0):
    return (datetime.now(timezone.utc).date() + timedelta(days=offset)).isoformat()


class Sourcing:
    def __init__(self, api, owner, project, event):
        self.api = api
        self.owner, self.project, self.event = owner, project, event
        self.path = f"/api/v1/production/projects/{project.id}"
        self.suppliers = f"/api/v1/production/organizations/{owner['organization'].id}/suppliers"
        self.parts = BOMAPI(api, owner["organization"])

    def call(self, method, suffix, status=200, **kwargs):
        return request(self.api.client, method, self.path + suffix, status, **kwargs)

    def supplier(self, name="Panel Workshop", **fields):
        return request(self.api.client, "POST", self.suppliers, 201,
                       json={"name": name, "contact_name": "Sales", "email": "quotes@example.test", **fields})

    def offering(self, supplier, status=201, **fields):
        return request(self.api.client, "POST", self.suppliers + f"/{supplier['id']}/offerings", status,
                       json={"name": "Panel batch", "kind": "material", "unit": "m2", **fields})

    def make_bom(self, *, approved=True, extras=()):
        definition = self.parts.material()
        obj, item, qty, spec, _ = mapped_takeoff(self.api, self.parts, definition=definition)
        bom = self.parts.revise(generate(qty, spec=spec), *extras)
        return (self.parts.approve(bom) if approved else bom), definition, obj, item, qty, spec

    def line(self, bom, **fields):
        return {"description": "Cut panels", "bom_line_id": bom["lines"][0]["id"], "quantity": "32", "unit": "m2",
                "unit_rate": "2", "specification": "12 mm plywood", "finish": "Matte", **fields}

    def quote(self, bom, supplier, status=201, **fields):
        return self.call("POST", "/quotes", status, params={"event_id": str(self.event.id)}, json={
            "bom_id": bom["id"], "supplier_id": supplier["id"], "reference": "Q-120", "expected_version": 0,
            "quoted_on": day(-1), "valid_until": day(10), "currency": "INR", "delivery_charge": "0",
            "lines": [self.line(bom)], **fields,
        })

    def quote_decision(self, quote, decision="approved", status=200):
        return self.call("POST", f"/quotes/{quote['id']}/decision", status,
                         json={"expected_version": quote["version"], "decision": decision})

    def procurement(self, bom, status=200, **fields):
        return self.call("POST", "/procurements/generate", status, json={"bom_id": bom["id"], **fields})

    def select(self, purchase, supplier, quote=None, status=200, **fields):
        values = {"expected_version": purchase["version"], "selected_supplier_id": supplier["id"] if supplier else None,
                  "quotation_id": quote["id"] if quote else None, **fields}
        return self.call("PUT", f"/procurements/{purchase['id']}/selection", status, json=values)

    def status(self, purchase, target, status=200, **fields):
        return self.call("POST", f"/procurements/{purchase['id']}/status", status,
                         json={"expected_version": purchase["version"], "status": target, **fields})


@pytest.fixture
def sourcing(production):
    return Sourcing(*production)


def test_budget_allocations_cost_bases_and_variance(sourcing, db_session):
    actual = manual(category="material", basis="actual", quantity="32", unit="m2", unit_rate="2.25",
                    source_reference="Paid workshop receipt")
    bom, *_ = sourcing.make_bom(extras=(actual,))
    supplier = sourcing.supplier()
    quote = sourcing.quote_decision(sourcing.quote(bom, supplier, delivery_charge="4"))
    sourcing.event.budget = 100
    sourcing.event.currency = "INR"
    db_session.commit()
    body = {"expected_version": 0, "currency": "inr", "allocations": {"material": "90"},
            "bom_ids": [bom["id"]], "quote_ids": [quote["id"]]}
    params = {"event_id": str(sourcing.event.id)}
    budget = sourcing.call("POST", "/budgets", 201, params=params, json=body)
    assert budget["amount"] == "100.00" and budget["unallocated"] == "10.00"
    assert budget["sources"]["legacy"]["kind"] == "event"
    assert budget["sources"]["boms"][0]["version"] == bom["version"]
    assert budget["sources"]["quotes"][0]["id"] == quote["id"]
    assert budget["summary"]["approved_amount"] is None
    assert {key: budget["summary"][key]["total"] for key in ("estimated", "quoted", "actual")} == {
        "estimated": "80.00", "quoted": "68.00", "actual": "72.00",
    }
    approved = sourcing.call("POST", f"/budgets/{budget['id']}/approve", json={"expected_version": 1})
    assert approved["summary"]["approved_amount"] == "100.00"
    assert approved["summary"]["remaining"] == "28.00" and approved["summary"]["actual_variance"] == "-28.00"
    assert approved["categories"]["material"]["remaining"] == "18.00"
    assert approved["categories"]["material"]["quoted_variance"] == "-22.00"
    assert approved["approved_by_user_id"] == str(sourcing.owner["user"].id)
    current = sourcing.call("POST", "/budgets", 201, params=params, json={**body, "expected_version": 1, "amount": "110"})
    assert current["version"] == 2 and current["approved_at"] is None
    history = sourcing.call("GET", "/budgets", params=params)
    assert [item["id"] for item in history] == [current["id"], approved["id"]]
    assert history[1]["summary"]["approved_amount"] == "100.00" and not history[1]["latest"]


def test_budget_missing_costs_stale_sources_and_revision_validation(sourcing, db_session):
    bom, *_ = sourcing.make_bom()
    body = {"expected_version": 0, "currency": "INR", "amount": "100", "allocations": {"material": "100"},
            "bom_ids": [bom["id"]]}
    sourcing.call("POST", "/budgets", 422, json={**body, "allocations": {"material": "101"}})
    sourcing.call("POST", "/budgets", 422, json={**body, "currency": "USD"})
    sourcing.call("POST", "/budgets", 422, json={**body, "bom_ids": [bom["id"], bom["id"]]})
    saved = sourcing.call("POST", "/budgets", 201, json=body)
    sourcing.call("POST", "/budgets", 409, json=body)
    approved = sourcing.call("POST", f"/budgets/{saved['id']}/approve", json={"expected_version": 1})
    assert approved["summary"]["actual"]["total"] is None and approved["summary"]["remaining"] is None
    assert approved["summary"]["quoted"]["priced_subtotal"] is None
    sourcing.parts.revise(manual(basis="actual"))
    after = sourcing.call("GET", f"/budgets/{saved['id']}")
    assert after["stale"] and after["stale_reasons"] == ["bom_changed"]
    assert after["summary"]["approved_amount"] == "100.00"
    sourcing.call("POST", "/budgets", 409, json={**body, "expected_version": 1})


def test_supplier_offerings_project_links_and_scope(sourcing, identity_factory):
    supplier = sourcing.supplier(categories=["Woodwork"], capabilities=["Cut to size"], coverage="Local delivery")
    definition = sourcing.parts.material()
    sourcing.offering(supplier, 422, minimum_quantity="4", unit=None)
    sourcing.offering(supplier, 422, material_id=definition["id"], unit="kg")
    offer = sourcing.offering(supplier, material_id=definition["id"], minimum_quantity="30", lead_time_days=4)
    service = sourcing.offering(supplier, kind="service", unit="h", name="Panel fitting")
    assert offer["details"]["minimum_quantity"] == "30"
    assert service["kind"] == "service"
    linked = sourcing.call("PUT", f"/suppliers/{supplier['id']}", json={"expected_version": 0, "note": "Approved sourcing list"})
    assert linked["version"] == 1 and linked["history"][0]["supplier"]["name"] == "Panel Workshop"
    updated = request(sourcing.api.client, "PUT", sourcing.suppliers + f"/{supplier['id']}",
                      json={"expected_version": 1, "name": "Workshop Trading"})
    assert updated["version"] == 2
    listing = sourcing.call("GET", "/suppliers")
    assert listing[0]["supplier"]["name"] == "Workshop Trading"
    assert listing[0]["history"][0]["supplier"]["name"] == "Panel Workshop"
    offering_path = sourcing.suppliers + f"/{supplier['id']}/offerings"
    assert len(request(sourcing.api.client, "GET", offering_path)) == 2
    request(sourcing.api.client, "PUT", offering_path + f"/{offer['id']}", 409,
            json={"expected_version": 2, "name": "Panel work", "kind": "material"})
    outsider = identity_factory()
    sourcing.api.client.headers["Authorization"] = "Bearer " + outsider["token"]
    request(sourcing.api.client, "GET", sourcing.suppliers + f"/{supplier['id']}", 404)
    sourcing.call("GET", "/suppliers", 404)
    sourcing.api.client.headers["Authorization"] = "Bearer " + sourcing.owner["token"]
    sourcing.call("PUT", f"/suppliers/{uuid4()}", 404, json={"expected_version": 0})


def test_quotation_decimal_comparison_documents_and_revisions(sourcing, monkeypatch, tmp_path, db_session):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "private-assets"))
    supplier = sourcing.supplier()
    bom, *_ = sourcing.make_bom()
    attachment = upload_jpeg(sourcing.api.client, sourcing.owner, sourcing.project.id)["asset_id"]
    chosen = sourcing.line(bom, discount_percent="10", tax_percent="5", supplier_total="60.48")
    quote = sourcing.quote(bom, supplier, attachment_id=attachment, lines=[chosen], delivery_charge="5",
                           supplier_total="65.48", lead_time_days=6, payment_terms="Net 30")
    assert quote["details"]["total"] == "65.48" and quote["details"]["basis"] == "supplier_submitted"
    assert quote["details"]["attachment"]["id"] == attachment and quote["details"]["attachment"]["sha256"]
    assert quote["lines"][0]["line_total"] == "60.48" and quote["lines"][0]["allocated_delivery"] == "5.00"
    assert quote["lines"][0]["comparison"]["price_difference"] == "-19.52"
    approved = sourcing.quote_decision(quote)
    assert approved["audit"][-1]["action"] == "approved"
    changed = sourcing.quote(bom, supplier, expected_version=1, lines=[{**chosen, "specification": "18 mm plywood"}])
    comparison = sourcing.call("GET", f"/boms/{bom['id']}/quote-comparison")
    assert [row["id"] for row in comparison["quotes"]] == [changed["id"]]
    assert comparison["quotes"][0]["lines"][0]["comparison"]["issues"] == ["specification_mismatch"]
    history = sourcing.call("GET", "/quotes", params={"history": True})
    assert history[1]["status"] == "approved"
    assert history[1]["details"]["attachment"]["id"] == attachment
    request(sourcing.api.client, "DELETE", f"/api/v1/assets/{attachment}", 200)
    asset = db_session.get(Asset, UUID(attachment))
    eligible_before = cleanup_deleted_assets(db=db_session)["eligible"]
    asset.deleted_at = datetime.now(timezone.utc) - timedelta(days=40)
    db_session.commit()
    assert cleanup_deleted_assets(db=db_session)["eligible"] == eligible_before
    assert "attachment_unavailable" in sourcing.call("GET", f"/quotes/{quote['id']}")["stale_reasons"]


def test_expired_and_incomplete_quotes_and_scope(sourcing):
    bom, *_ = sourcing.make_bom()
    supplier = sourcing.supplier()
    sourcing.quote(bom, supplier, status=422, quoted_on=day(0), valid_until=day(-2))
    sourcing.quote(bom, supplier, status=422, currency="USD")
    sourcing.quote(bom, supplier, status=404, lines=[sourcing.line(bom, bom_line_id=str(uuid4()))])
    sourcing.quote(bom, supplier, status=422, lines=[sourcing.line(bom), sourcing.line(bom)])
    expired = sourcing.quote(bom, supplier, quoted_on=day(-5), valid_until=day(-1))
    assert expired["expired"]
    sourcing.quote_decision(expired, status=409)
    incomplete = sourcing.quote(bom, supplier, reference="Partial", valid_until=None, delivery_charge=None,
                                lines=[sourcing.line(bom, unit_rate=None, specification=None)])
    assert incomplete["details"]["total"] is None
    assert set(incomplete["details"]["issues"]) == {"validity_missing", "delivery_charge_missing", "line_information_incomplete"}
    assert "specification_missing" in incomplete["lines"][0]["comparison"]["issues"]
    sourcing.quote_decision(incomplete, status=409)
    wrong_total = sourcing.quote(bom, supplier, reference="Incorrect", supplier_total="70")
    assert wrong_total["details"]["issues"] == ["supplier_total_mismatch"]
    assert sourcing.quote_decision(wrong_total, decision="rejected")["status"] == "rejected"


def test_event_and_asset_scope_rejected_before_quote_capture(sourcing, db_session, monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "private-assets"))
    bom, *_ = sourcing.make_bom()
    supplier = sourcing.supplier()
    unrelated = make_project(db_session, sourcing.owner, "Different project")
    attachment = upload_jpeg(sourcing.api.client, sourcing.owner, unrelated.id)["asset_id"]
    sourcing.quote(bom, supplier, status=404, attachment_id=attachment)
    other = Event(name="Other event", project_id=sourcing.project.id,
                  organization_id=sourcing.owner["organization"].id)
    db_session.add(other)
    db_session.commit()
    params = {"event_id": str(other.id)}
    sourcing.call("POST", "/procurements/generate", 404, params=params, json={"bom_id": bom["id"]})
    sourcing.call("POST", "/budgets", 404, params=params,
                  json={"expected_version": 0, "currency": "INR", "amount": "100", "bom_ids": [bom["id"]]})


def test_supplier_deactivation_keeps_quote_snapshot_and_blocks_approval(sourcing):
    bom, *_ = sourcing.make_bom()
    supplier = sourcing.supplier()
    sourcing.call("PUT", f"/suppliers/{supplier['id']}", json={"expected_version": 0})
    quote = sourcing.quote(bom, supplier)
    sourcing.call("PUT", f"/suppliers/{supplier['id']}", json={"expected_version": 1, "active": False})
    saved = sourcing.call("GET", f"/quotes/{quote['id']}")
    assert saved["stale_reasons"] == ["supplier_unavailable"]
    assert saved["details"]["supplier"]["name"] == supplier["name"]
    sourcing.quote_decision(quote, status=409)
    sourcing.quote(bom, supplier, status=409, reference="Q-121")


def test_procurement_lifecycle_and_authorization(sourcing, identity_factory):
    bom, *_ = sourcing.make_bom(approved=False)
    sourcing.procurement(bom, status=409)
    bom = sourcing.parts.approve(bom)
    requirement = sourcing.procurement(bom, required_delivery_date=day(12), storage_details="Dock B")[0]
    assert requirement["issues"] == ["supplier_missing", "quotation_missing"]
    assert requirement["requirement"]["source"]["specification"]["technical"]
    assert sourcing.procurement(bom)[0]["id"] == requirement["id"]
    supplier, second = sourcing.supplier(), sourcing.supplier("Second workshop")
    quote = sourcing.quote_decision(sourcing.quote(bom, supplier, delivery_charge="4", lead_time_days=5))
    sourcing.select(requirement, second, quote, status=422)
    selected = sourcing.select(requirement, supplier, quote, preferred_supplier_id=supplier["id"],
                               alternative_supplier_ids=[second["id"]])
    assert selected["status"] == "sourcing" and selected["selection"]["required_delivery_date"] == day(12)
    assert len(selected["selection"]["supplier_snapshots"]) == 2
    designer = identity_factory(role="designer", organization=sourcing.owner["organization"])
    sourcing.api.client.headers["Authorization"] = "Bearer " + designer["token"]
    sourcing.status(selected, "approved", 403)
    sourcing.api.client.headers["Authorization"] = "Bearer " + sourcing.owner["token"]
    approved = sourcing.status(selected, "approved")
    assert approved["approval"]["approved_cost"] == "68.00"
    assert approved["approval"]["conditions"]["lead_time_days"] == 5
    assert approved["approval"]["quotation_version"] == 1
    sourcing.select(approved, supplier, quote, status=409)
    sourcing.status(approved, "ordered", 422)
    ordered = sourcing.status(approved, "ordered", reference="External PO 12", recorded_date=day())
    delivered = sourcing.status(ordered, "delivered", reference="Packing slip 62", recorded_date=day())
    assert delivered["status"] == "delivered" and delivered["approval"] == approved["approval"]
    assert [row["action"] for row in delivered["audit"]] == [
        "generated", "selection_updated", "approved", "ordered", "delivered",
    ]
    assert sourcing.call("GET", "/procurements", params={"status": "delivered"})[0]["id"] == delivered["id"]


def test_minimum_order_and_surplus_need_explicit_review(sourcing):
    bom, definition, *_ = sourcing.make_bom()
    supplier = sourcing.supplier()
    offer = sourcing.offering(supplier, material_id=definition["id"], minimum_quantity="40", lead_time_days=3)
    purchase = sourcing.procurement(bom)[0]
    quote = sourcing.quote_decision(sourcing.quote(bom, supplier, lines=[sourcing.line(bom, offering_id=offer["id"])]))
    selected = sourcing.select(purchase, supplier, quote)
    assert sourcing.status(selected, "approved", 409)["detail"]["code"] == "procurement_minimum_order"
    larger = sourcing.quote_decision(sourcing.quote(bom, supplier, expected_version=1, lines=[
        sourcing.line(bom, offering_id=offer["id"], quantity="40"),
    ]))
    selected = sourcing.select(selected, supplier, larger)
    assert sourcing.status(selected, "approved", 409)["detail"]["code"] == "procurement_quantity_review"
    selected = sourcing.select(selected, supplier, larger, accept_quantity_difference=True)
    assert sourcing.status(selected, "approved")["approval"]["approved_cost"] == "80.00"


def test_multiline_delivery_needs_explicit_allocation(sourcing):
    second = manual(category="labour", quantity="2", unit="h", name="Delivery handling")
    bom, *_ = sourcing.make_bom(extras=(second,))
    supplier = sourcing.supplier()
    purchase = sourcing.procurement(bom)[0]
    lines = [sourcing.line(bom), sourcing.line(bom, bom_line_id=bom["lines"][1]["id"], quantity="2", unit="h")]
    quote = sourcing.quote_decision(sourcing.quote(bom, supplier, lines=lines, delivery_charge="8"))
    assert quote["details"]["total"] == "76.00" and not quote["details"]["delivery_allocated"]
    selected = sourcing.select(purchase, supplier, quote)
    assert sourcing.status(selected, "approved", 409)["detail"]["code"] == "procurement_price_incomplete"
    revised = sourcing.quote_decision(sourcing.quote(
        bom, supplier, expected_version=1, delivery_charge="8",
        lines=[{**lines[0], "delivery_amount": "5"}, {**lines[1], "delivery_amount": "3"}],
    ))
    assert revised["details"]["delivery_allocated"]
    assert sourcing.status(sourcing.select(selected, supplier, revised), "approved")["approval"]["approved_cost"] == "69.00"


def test_value_engineering_review_and_application(sourcing, db_session, identity_factory):
    bom, original, *_ = sourcing.make_bom()
    alternative = sourcing.parts.material(name="Alternate panel", specification="Alternate sample construction")
    supplier = sourcing.supplier()
    quote = sourcing.quote_decision(sourcing.quote(bom, supplier, lines=[
        sourcing.line(bom, material_id=alternative["id"], specification="Alternate sample construction", unit_rate="1.50"),
    ]))
    purchase = sourcing.procurement(bom)[0]
    purchase = sourcing.select(purchase, supplier, quote)
    assert sourcing.status(purchase, "approved", 409)["detail"]["code"] == "procurement_substitution_required"
    design = DesignArtifact(project_id=sourcing.project.id, event_id=sourcing.event.id, event_scope_key=str(sourcing.event.id),
                            module="style", version=1, source="user_edited", content={"intent": "Matte finish"},
                            context_fingerprint="fixture", created_by_user_id=sourcing.owner["user"].id)
    db_session.add(design)
    db_session.commit()
    path = f"/procurements/{purchase['id']}/proposals"
    fields = {
        "expected_procurement_version": purchase["version"], "quotation_id": quote["id"],
        "design_artifact_id": str(design.id), "rationale": "Reduce material cost",
        "specification_comparison": "Compare the two signed shop drawings", "quantity_impact": "Same measured supply",
        "visual_impact": "Sample requires finish review", "technical_impact": "Use the supplied review record",
        "production_impact": "Install per reviewed shop details", "lead_time_impact": "Supplier confirmation pending",
        "risks": "Check the approved sample before use",
    }
    unevidenced = sourcing.call("POST", path, 201, json=fields)
    sourcing.call("POST", path + f"/{unevidenced['id']}/decision", 409, json={
        "expected_version": 1, "expected_procurement_version": purchase["version"], "decision": "approved",
    })
    proposed = sourcing.call("POST", path, 201, json={**fields, "evidence_reference": "Signed review and sample SR-1"})
    assert proposed["proposal"]["cost_difference"] == "-32.00"
    assert proposed["proposal"]["original"]["material_id"] == original["id"]
    assert proposed["proposal"]["alternative"]["material"]["id"] == alternative["id"]
    assert proposed["proposal"]["design_artifact"]["id"] == str(design.id)
    decision_path = path + f"/{proposed['id']}/decision"
    body = {"expected_version": 1, "expected_procurement_version": purchase["version"], "decision": "approved"}
    sourcing.call("POST", decision_path, 409, json={**body, "decision": "applied"})
    designer = identity_factory(role="designer", organization=sourcing.owner["organization"])
    sourcing.api.client.headers["Authorization"] = "Bearer " + designer["token"]
    sourcing.call("POST", decision_path, 403, json=body)
    sourcing.api.client.headers["Authorization"] = "Bearer " + sourcing.owner["token"]
    approved = sourcing.call("POST", decision_path, json=body)
    current = sourcing.call("GET", f"/procurements/{purchase['id']}")
    assert current["selection"]["substitution"] is None
    applied = sourcing.call("POST", decision_path, json={**body, "expected_version": approved["version"], "decision": "applied"})
    assert applied["status"] == "applied"
    current = sourcing.call("GET", f"/procurements/{purchase['id']}")
    assert current["selection"]["substitution"]["proposal_id"] == proposed["id"]
    assert current["requirement"] == purchase["requirement"]
    assert sourcing.status(current, "approved")["approval"]["approved_cost"] == "48.00"
    assert sourcing.parts.get(bom)["lines"][0]["total"] == "80.00"


def test_upstream_bom_changes_mark_procurement_and_quote_stale(sourcing):
    bom, *_ = sourcing.make_bom()
    supplier = sourcing.supplier()
    purchase = sourcing.procurement(bom)[0]
    quote = sourcing.quote_decision(sourcing.quote(bom, supplier))
    selected = sourcing.select(purchase, supplier, quote)
    newer = sourcing.parts.revise(manual())
    read = sourcing.call("GET", f"/procurements/{selected['id']}")
    assert read["stale_reasons"] == ["bom_changed", "quotation_changed"]
    assert sourcing.call("GET", f"/quotes/{quote['id']}")["stale_reasons"] == ["bom_changed"]
    sourcing.status(selected, "approved", 409)
    sourcing.procurement(bom, status=409)
    assert newer["version"] == 2


def test_record_external_order_against_preserved_approval(sourcing):
    bom, *_ = sourcing.make_bom()
    supplier = sourcing.supplier()
    quote = sourcing.quote_decision(sourcing.quote(bom, supplier))
    purchase = sourcing.procurement(bom)[0]
    approved = sourcing.status(sourcing.select(purchase, supplier, quote), "approved")
    sourcing.parts.revise(manual())
    opened = sourcing.call("GET", f"/procurements/{purchase['id']}")
    assert opened["stale"] and opened["approval"]["approved_cost"] == "64.00"
    ordered = sourcing.status(opened, "ordered", reference="Previously placed order PO-12", recorded_date=day())
    assert ordered["stale"] and ordered["approval"] == approved["approval"]


def test_quotation_history_protects_unapproved_bom_source(sourcing):
    bom = sourcing.parts.revise(manual())
    quote = sourcing.quote(bom, sourcing.supplier(), lines=[
        sourcing.line(bom, quantity="2.50", unit="h"),
    ])
    response = sourcing.api.client.delete(sourcing.api.path, params={"expected_version": sourcing.api.version})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "quotation_history"
    assert sourcing.call("GET", f"/quotes/{quote['id']}")["id"] == quote["id"]


def test_tenant_boundaries_and_request_uniqueness(sourcing, db_session, identity_factory):
    bom, *_ = sourcing.make_bom()
    supplier = sourcing.supplier()
    quote = sourcing.quote(bom, supplier)
    purchase = sourcing.procurement(bom)[0]
    row = db_session.get(ProductionProcurement, UUID(purchase["id"]))
    values = {column.key: getattr(row, column.key) for column in ProductionProcurement.__table__.columns if column.key != "id"}
    db_session.add(ProductionProcurement(**values))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    outsider = identity_factory()
    sourcing.api.client.headers["Authorization"] = "Bearer " + outsider["token"]
    foreign_suppliers = f"/api/v1/production/organizations/{outsider['organization'].id}/suppliers"
    foreign = request(sourcing.api.client, "POST", foreign_suppliers, 201, json={"name": "Another supplier"})
    sourcing.call("GET", f"/quotes/{quote['id']}", 404)
    sourcing.call("GET", f"/procurements/{purchase['id']}", 404)
    sourcing.call("POST", "/budgets", 404, json={"expected_version": 0, "currency": "INR", "amount": "5"})
    sourcing.api.client.headers["Authorization"] = "Bearer " + sourcing.owner["token"]
    sourcing.quote(bom, foreign, status=404)
    sourcing.select(purchase, foreign, status=404)
    sourcing.api.client.headers.pop("Authorization")
    sourcing.call("GET", "/budgets", 401)
