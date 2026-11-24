from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.production_bom import ProductionBOMLine
from app.services.production_costing import calculate_cost, convert_quantity, required_quantity
from test_phase9_spatial import create_object, create_scene, make_project
from test_production import ProductionAPI, production, request
from test_production_quantification import calculate, patch_object, quantity_request, revision


class BOMAPI:
    def __init__(self, api, organization):
        self.api = api
        self.catalog = f"/api/v1/production/organizations/{organization.id}/materials"
        self.path = api.path + "/boms"
        self.version = 0
        self.currency = "INR"
        self.precision = 2

    def material(self, status=201, **fields):
        body = {"name": "Birch panel", "category": "sheet goods", "unit": "m2",
                "unit_rate": "2.50", "currency": "INR", "specification": "12 mm plywood", **fields}
        return request(self.api.client, "POST", self.catalog, status, json=body)

    def update(self, definition, status=200, **fields):
        body = {key: definition[key] for key in ("name", "description", "category", "unit", "unit_rate", "currency",
                                                "status", "specification", "finish", "allowance_percent",
                                                "supplier_reference", "source_reference")}
        return request(self.api.client, "PUT", self.catalog + "/" + definition["id"], status,
                       json={"expected_version": definition["version"], **body, **fields})

    def revise(self, *changes, status=200, **fields):
        row = request(self.api.client, "POST", self.path, status, json={
            "expected_record_version": self.api.version, "expected_bom_version": self.version,
            "scene_revision": revision(self.api), "currency": self.currency, "currency_precision": self.precision,
            "changes": changes, **fields,
        })
        if status == 200:
            self.version = row["version"]
        return row

    def approve(self, bom, status=200):
        return request(self.api.client, "POST", self.path + "/" + bom["id"] + "/approve", status, json={
            "expected_record_version": self.api.version, "expected_bom_version": self.version,
            "scene_revision": revision(self.api),
        })

    def get(self, bom):
        return request(self.api.client, "GET", self.path + "/" + bom["id"])


def create_sheet(api, item_id, *, supersedes_id=None, source="user_entered"):
    path = f"/items/{item_id}/specifications"
    extra = {"target": "object"} if source == "spatial_geometry" else {}
    spec = api.write("POST", path, 201, name="Fabrication reference", content="Use the assigned material.",
                     supersedes_id=supersedes_id,
                     technical={"source": source, "scene_revision": revision(api), "finish": "Matte", **extra})["id"]
    api.write("POST", path + f"/{spec}/approve", scene_revision=revision(api))
    return spec


def mapped_takeoff(api, bom, *, definition=None, overrides=None, sheet=True):
    obj = create_object(api.client, api.scene_id)
    material = None
    if definition is not None:
        material = api.write("POST", "/materials", 201, name="Deck panel", definition_id=definition["id"],
                             overrides=overrides)["id"]
    item = api.item("Stage surface", spatial_object_id=obj["id"], material_id=material)
    qty = calculate(api, quantity_request(item, "plan_area"))[0]
    spec = create_sheet(api, item) if sheet else None
    return obj, item, qty, spec, material


def generate(quantity, *, item=None, spec=None, **fields):
    return {"type": "generate", "quantity_id": quantity["id"], "item_id": item, "specification_id": spec, **fields}


def manual(**fields):
    return {"type": "manual", "name": "Shop labour", "basis": "estimated", "category": "labour", "unit": "h",
            "quantity": "2.50", "unit_rate": "18.125", "source_reference": "Shop worksheet dated 2026-11-24", **fields}


def codes(line):
    return {issue["code"] for issue in line["issues"]}


def test_catalogue_revisions_and_project_overrides(production):
    api, owner, _, _ = production
    bom = BOMAPI(api, owner["organization"])
    bom.material(422, unit="carton")
    bom.material(422, unit_rate="-1")
    bom.material(422, unit_rate="1", currency=None)
    bom.material(422, allowance_percent="1001")
    bom.material(422, specification="bad\x00text")
    definition = bom.material(currency="inr", finish="Clear", supplier_reference="Shop reference 4")
    assert definition["currency"] == "INR" and definition["version"] == 1 and definition["revised_at"]
    assert definition["entered_by_user_id"] == str(owner["user"].id)
    assignment = api.write("POST", "/materials", 201, name="Dress panel", definition_id=definition["id"],
                           overrides={"unit_rate": "3.7500", "currency": "inr", "finish": "Dark stain"})["id"]
    details = request(api.client, "GET", api.path + "/materials/" + assignment)
    assert details["effective"]["unit_rate"] == "3.75" and details["effective"]["finish"] == "Dark stain"
    assert details["effective"]["specification"] == "12 mm plywood"
    second = bom.update(definition, unit_rate="2.75", specification="18 mm plywood")
    assert second["version"] == 2
    bom.update(definition, 409)
    first = request(api.client, "GET", bom.catalog + "/" + definition["id"], params={"version": 1})
    assert first["specification"] == "12 mm plywood" and Decimal(first["unit_rate"]) == Decimal("2.50")
    assert first["current_version"] == 2
    details = request(api.client, "GET", api.path + "/materials/" + assignment)
    assert details["effective"]["unit_rate"] == "3.75" and details["effective"]["specification"] == "18 mm plywood"
    bom.update(second, unit="ft2")
    details = request(api.client, "GET", api.path + "/materials/" + assignment)
    assert details["effective"]["unit_rate"] is None and details["effective"]["override_rate_unit"] == "m2"
    api.write("PUT", f"/materials/{assignment}", name="Dress panel", overrides=None)
    details = request(api.client, "GET", api.path + "/materials/" + assignment)
    assert details["version"] == 2 and details["effective"]["unit_rate"] == "2.75"
    assert len(request(api.client, "GET", bom.catalog, params={"status": "active"})) == 1


def test_hierarchy_breakdown_and_cycle_validation(production):
    api, _, _, _ = production
    root = api.item("Build", kind="assembly")
    nested = api.item("Deck assembly", kind="assembly", parent_item_id=root)
    piece = api.item("Panel", parent_item_id=nested)
    api.measure(piece, "count", value="8", unit="each")
    assert api.write("PUT", f"/items/{root}", 422, name="Build", parent_item_id=nested)["detail"]
    api.write("PUT", f"/items/{root}", 422, name="Build", parent_item_id=piece)
    api.write("PUT", f"/items/{root}", 422, name="Build", parent_item_id=root)
    breakdown = request(api.client, "GET", api.path + f"/items/{root}/breakdown")
    assert [(row["id"], row["depth"]) for row in breakdown] == [(root, 0), (nested, 1), (piece, 2)]
    assert breakdown[2]["measurements"][0]["value"] == "8.000000"
    api.write("PUT", f"/items/{piece}", name="Panel", parent_item_id=root)
    breakdown = request(api.client, "GET", api.path + f"/items/{root}/breakdown")
    assert {row["id"]: row["depth"] for row in breakdown}[piece] == 1


def test_generation_cost_formula_and_approved_part2_input(production):
    api, owner, project, event = production
    bom = BOMAPI(api, owner["organization"])
    material = bom.material(allowance_percent="10", supplier_reference="PX-12")
    obj, item, qty, spec, usage = mapped_takeoff(api, bom, definition=material)
    api.sources(revision(api))
    api.sources(revision(api), action="approve")
    change = generate(qty, spec=spec, discount_percent="10", tax_percent="5")
    output = bom.revise(change)
    line = output["lines"][0]
    assert line["total"] == "83.16" and line["quantity"] == "35.200000"
    assert line["cost"]["base"] == "88.00" and line["cost"]["discount"] == "8.80" and line["cost"]["tax"] == "3.96"
    assert "quantity × rate" in line["cost"]["formula"]
    trace = line["inputs"]["dependencies"]
    assert trace["quantity"]["id"] == qty["id"] and trace["quantity"]["source"]["target_id"] == obj["id"]
    assert trace["component"]["id"] == item and trace["specification"]["id"] == spec
    assert trace["material"]["id"] == usage and trace["material"]["definition_version"] == 1
    assert output["project_id"] == str(project.id) and output["event_id"] == str(event.id)
    assert output["costs"][0]["total"] == "83.16" and not output["stale"]
    assert bom.revise(change)["id"] == output["id"]
    approved = bom.approve(output)
    assert approved["approved_by_user_id"] == str(owner["user"].id)
    assert bom.get(output)["lines"][0]["total"] == "83.16"


def test_required_mapping_specification_and_missing_rate_are_visible(production):
    api, owner, _, _ = production
    bom = BOMAPI(api, owner["organization"])
    definition = bom.material(unit_rate=None, currency=None)
    _, unassigned, qty_one, _, _ = mapped_takeoff(api, bom, sheet=False)
    _, mapped, qty_two, spec, _ = mapped_takeoff(api, bom, definition=definition)
    snapshot = bom.revise(generate(qty_one), generate(qty_two, spec=spec), manual())
    first, second, priced = snapshot["lines"]
    assert first["inputs"]["item_id"] == unassigned and {"missing_material_mapping", "missing_specification"} <= codes(first)
    assert second["inputs"]["item_id"] == mapped and codes(second) == {"missing_rate"}
    assert second["quantity"] == "32.000000" and second["unit_rate"] is None and second["total"] is None
    assert priced["total"] == "45.31"
    assert snapshot["costs"] == [{"basis": "estimated", "priced_subtotal": "45.31", "total": None,
                                   "unpriced_lines": 2, "stale_lines": 0}]
    bom.approve(snapshot, 409)
    bom.update(definition, unit_rate="0", currency="INR")
    assert bom.get(snapshot)["stale"]
    revised = bom.revise(generate(qty_two, spec=spec), {"type": "remove", "line_id": first["id"]})
    assert revised["lines"][0]["total"] == "0.00"
    assert revised["costs"][0]["total"] == "45.31"


def test_component_consumption_and_unit_conversion(production):
    api, owner, _, _ = production
    bom = BOMAPI(api, owner["organization"])
    coating = bom.material(unit="kg", unit_rate="6.25")
    carpet = bom.material(name="Carpet", unit="ft2", unit_rate="1")
    obj = create_object(api.client, api.scene_id, dimensions={"width_m": 1, "depth_m": 1, "height_m": "0.1"})
    root = api.item("Floor build", kind="assembly", spatial_object_id=obj["id"])
    area = calculate(api, quantity_request(root, "plan_area"))[0]
    coating_usage = api.write("POST", "/materials", 201, name="Coating", definition_id=coating["id"])["id"]
    carpet_usage = api.write("POST", "/materials", 201, name="Carpet", definition_id=carpet["id"])["id"]
    coating_item = api.item("Coating", parent_item_id=root, material_id=coating_usage)
    carpet_item = api.item("Carpet", parent_item_id=root, material_id=carpet_usage)
    unconnected = api.item("Unrelated", material_id=carpet_usage)
    create_sheet(api, coating_item)
    create_sheet(api, carpet_item)
    bom.revise(generate(area, item=unconnected), status=422)
    invalid = bom.revise(generate(area, item=coating_item))["lines"][0]
    assert "incompatible_quantity" in codes(invalid)
    valid = bom.revise(generate(area, item=coating_item,
                                consumption={"quantity": "0.25", "unit": "kg", "per_quantity": "1", "per_unit": "m2"}),
                       generate(area, item=carpet_item))
    assert [line["quantity"] for line in valid["lines"]] == ["0.250000", "10.763910"]
    assert [line["total"] for line in valid["lines"]] == ["1.56", "10.76"]
    assert valid["lines"][0]["inputs"]["dependencies"]["component"]["parent_ids"] == [root]


def test_generated_rates_keep_their_currency(production):
    api, owner, _, _ = production
    bom = BOMAPI(api, owner["organization"])
    definition = bom.material(currency="USD")
    _, _, qty, spec, _ = mapped_takeoff(api, bom, definition=definition)
    mismatched = bom.revise(generate(qty, spec=spec))
    line = mismatched["lines"][0]
    assert line["unit_rate_currency"] == "USD" and line["total"] is None
    assert codes(line) == {"currency_mismatch"}
    bom.update(definition, currency="INR")
    corrected = bom.revise(generate(qty, spec=spec))
    assert corrected["lines"][0]["unit_rate_currency"] == "INR"
    assert corrected["lines"][0]["total"] == "80.00"
    assert bom.get(mismatched)["lines"][0]["unit_rate_currency"] == "USD"


def test_manual_and_approved_history_protected(production):
    api, owner, project, event = production
    bom = BOMAPI(api, owner["organization"])
    _, _, quantity, spec, _ = mapped_takeoff(api, bom, definition=bom.material())
    change = generate(quantity, spec=spec)
    base = bom.revise(change, manual(basis="quoted", source_reference="Vendor quote AQ-16"))
    bom.approve(base)
    lineage = base["lines"][0]["line_key"]
    bumped = bom.revise(manual(line_id=base["lines"][0]["id"], name="Adjusted plywood estimate", category="material",
                               quantity="35", unit="m2", unit_rate="3", source_reference="Signed takeoff adjustment"))
    assert bumped["version"] == 2 and bumped["approved_at"] is None
    assert bumped["lines"][0]["line_key"] == lineage and bumped["lines"][0]["origin"] == "manual"
    assert bumped["lines"][0]["entered_by_user_id"] == str(owner["user"].id)
    assert bom.revise(change, status=409)["detail"]["code"] == "bom_manual_line"
    recalculated = bom.revise({**change, "replace_manual": True}, manual(
        basis="actual", unit="h", quantity="4", unit_rate="20", source_reference="Recorded timesheet TS-14",
    ))
    assert {row["basis"]: row["total"] for row in recalculated["costs"]} == {
        "estimated": "80.00", "quoted": "45.31", "actual": "80.00",
    }
    previous = bom.get(base)
    assert previous["approved_at"] and not previous["latest"]
    assert bom.get(bumped)["lines"][0]["total"] == "105.00"
    summary = request(api.client, "GET", f"/api/v1/production/projects/{project.id}/cost-summary",
                      params={"event_id": str(event.id)})
    assert summary["records"][0]["id"] == recalculated["id"]
    assert summary["approved_records"][0]["id"] == base["id"]
    assert {row["basis"] for row in summary["totals"]} == {"estimated", "quoted", "actual"}


def test_quantity_specification_and_rate_staleness(production):
    api, owner, _, _ = production
    bom = BOMAPI(api, owner["organization"])
    definition = bom.material()
    obj, item, qty, spec, _ = mapped_takeoff(api, bom, definition=definition)
    old = bom.revise(generate(qty, spec=spec))
    bom.approve(old)
    definition = bom.update(definition, unit_rate="3")
    assert bom.get(old)["lines"][0]["stale_reasons"] == ["material_changed"]
    sheet = request(api.client, "GET", api.path + f"/items/{item}/specifications/{spec}")
    assert not sheet["stale"]
    updated_rates = bom.revise(generate(qty, spec=spec))
    assert updated_rates["lines"][0]["total"] == "96.00"
    patch_object(api, obj, dimensions={"width_m": 10, "depth_m": 4, "height_m": 1})
    assert set(bom.get(updated_rates)["lines"][0]["stale_reasons"]) == {"quantity_changed", "specification_changed"}
    assert bom.approve(updated_rates, 409)["detail"]["code"] == "bom_unready"
    latest_qty = calculate(api, quantity_request(item, "plan_area"))[0]
    new_spec = create_sheet(api, item, supersedes_id=spec)
    latest = bom.revise(generate(latest_qty, spec=new_spec))
    assert latest["lines"][0]["total"] == "120.00" and not latest["stale"]
    assert bom.get(old)["lines"][0]["total"] == "80.00"
    assert latest["lines"][0]["line_key"] == old["lines"][0]["line_key"]


def test_scope_currency_and_cost_rollups(production, db_session, identity_factory):
    api, owner, project, event = production
    bom = BOMAPI(api, owner["organization"])
    event.currency = "INR"
    db_session.commit()
    bom.revise(manual(), currency="USD", status=422)
    bom.revise(manual(unit="each", quantity="1.5"), status=422)
    bom.revise(manual(tax_percent="101"), status=422)
    bom.precision = 0
    valid = bom.revise(manual(unit="each", quantity="1", unit_rate="2.50"))
    assert valid["lines"][0]["total"] == "3"
    bom.revise(manual(), currency_precision=2, status=422)
    bom.revise(manual(), expected_bom_version=0, status=409)
    alternative = ProductionAPI(api.client, {"id": api.scene_id})
    alternative_bom = BOMAPI(alternative, owner["organization"])
    alternative_bom.revise(manual())
    path = f"/api/v1/production/projects/{project.id}/cost-summary"
    summary = request(api.client, "GET", path)
    assert summary["ambiguous_scene_ids"] == [api.scene_id] and summary["totals"] == []
    filtered = request(api.client, "GET", path, params={"record_id": api.record["id"]})
    assert filtered["totals"][0]["total"] == "3"

    outsider = identity_factory()
    api.client.headers["Authorization"] = "Bearer " + outsider["token"]
    request(api.client, "GET", bom.path, 404)
    request(api.client, "GET", bom.catalog, 404)
    request(api.client, "GET", path, 404)
    member = identity_factory(role="member", organization=owner["organization"])
    api.client.headers["Authorization"] = "Bearer " + member["token"]
    assert bom.get(valid)["id"] == valid["id"]
    bom.revise(manual(), status=403)
    bom.material(403)
    designer = identity_factory(role="designer", organization=owner["organization"])
    api.client.headers["Authorization"] = "Bearer " + designer["token"]
    bom.approve(valid, 403)
    api.client.headers.pop("Authorization")
    request(api.client, "GET", path, 401)


def test_cross_scope_references_and_duplicate_database_keys(production, db_session, identity_factory):
    api, owner, _, _ = production
    bom = BOMAPI(api, owner["organization"])
    _, _, quantity, spec, _ = mapped_takeoff(api, bom, definition=bom.material())
    line = generate(quantity, spec=spec)
    bom.revise(line, line, status=409)
    assert request(api.client, "GET", bom.path) == []
    accepted = bom.revise(line)
    original = db_session.get(ProductionBOMLine, UUID(accepted["lines"][0]["id"]))
    values = {column.key: getattr(original, column.key) for column in ProductionBOMLine.__table__.columns if column.key != "id"}
    db_session.add(ProductionBOMLine(**values))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    other = ProductionAPI(api.client, {"id": api.scene_id})
    other_bom = BOMAPI(other, owner["organization"])
    other_bom.revise(line, status=404)
    request(api.client, "GET", other_bom.path + "/" + accepted["id"], 404)
    outsider = identity_factory()
    api.client.headers["Authorization"] = "Bearer " + outsider["token"]
    foreign = BOMAPI(other, outsider["organization"]).material()
    api.client.headers["Authorization"] = "Bearer " + owner["token"]
    api.write("POST", "/materials", 404, name="Foreign", definition_id=foreign["id"])


@pytest.mark.parametrize("value,original,target,expected", [
    ("1", "m", "cm", "100.000000"),
    ("1", "m2", "ft2", "10.763910"),
    ("1", "lb", "kg", "0.453592"),
    ("1", "m3", "cm3", "1000000.000000"),
])
def test_pricing_unit_conversions(value, original, target, expected):
    converted = convert_quantity(Decimal(value), original, target)
    assert required_quantity(converted, target, Decimal(0)) == Decimal(expected)


def test_decimal_rounding_rules():
    assert calculate_cost(Decimal("1"), Decimal("1.005"), Decimal("0"), Decimal("0"), 2)["total"] == "1.01"
    assert calculate_cost(Decimal("1"), Decimal("1.005"), Decimal("0"), Decimal("0"), 3)["total"] == "1.005"
    with pytest.raises(ValueError):
        convert_quantity(Decimal(2), "m2", "kg")
