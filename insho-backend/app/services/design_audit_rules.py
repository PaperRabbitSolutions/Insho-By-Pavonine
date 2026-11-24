"""Evidence-backed dimension assessments over captured design sources."""

from decimal import Decimal, InvalidOperation
import json
from uuid import UUID, uuid5

from pydantic_core import to_jsonable_python

from app.schemas.design_audit import DimensionResult, Evaluator, Evidence, Finding, Recommendation, SourceReference


CONFIGURATION_VERSION = "28.1-v1"
SCORE_SCALE = {1: "An evidenced issue in the stated criterion", 2: "Mixed evidence for the stated criterion",
               3: "The stated criterion is supported by the reviewed evidence"}
SAFETY_SCOPE = "Preliminary decision support. Obtain qualified engineering, venue and safety review before fabrication or installation."
_NAMESPACE = UUID("d273ebb6-7762-4f55-b085-2d18c736cc68")
_SPATIAL_RULES = {"geometry", "transforms", "hierarchy", "venue_boundary", "zone_containment", "zone_overlap", "collision", "clearance"}
_RISK_OBJECTS = {"rigging", "truss", "structure", "stage", "scenic", "electrical", "lighting", "led", "led_wall", "led_surface", "led_screen"}
_SEVERITY = {"info": 0, "review": 1, "high": 2}


def ref(kind, key, revision=None, field=None):
    return SourceReference(kind=kind, id=key, revision=revision, field=field)


def evidence(source, statement, value=None, *, origin="observed"):
    data = to_jsonable_python(value)
    key = json.dumps([source.model_dump(mode="json"), statement, data, origin], sort_keys=True, separators=(",", ":"))
    return Evidence(id=uuid5(_NAMESPACE, key), origin=origin, source=source, statement=statement, value=data)


class Assessment:
    def __init__(self, ctx, dimension, criterion, *, method="deterministic"):
        self.ctx = ctx
        self.dimension = dimension
        self.evaluator = Evaluator(method=method, criterion=criterion)
        self.evidence = {}
        self.findings = []
        self.recommendations = []
        self.missing = []
        self.uncertainty = []

    def add(self, items):
        for item in items:
            self.evidence[item.id] = item

    def _items(self, items):
        if not items:
            items = [evidence(self.ctx.ref(), "The selected design version is the reference for this finding.")]
        self.add(items)
        return items

    def recommend(self, code, action, rationale, items, *, targets=None, dependencies=None, professional=False):
        items = self._items(items)
        self.recommendations.append(Recommendation(finding_code=code, action=action, rationale=rationale,
            targets=targets or [item.source for item in items], affected_dependencies=dependencies or [self.ctx.ref()],
            evidence_ids=[item.id for item in items], review_required="professional" if professional else "editor"))

    def flag(self, code, explanation, action, items, *, severity="review", origin="derived",
             source_finding_id=None, targets=None, dependencies=None, professional=False):
        supported = self._items(items)
        self.findings.append(Finding(code=code, origin=origin, severity=severity, explanation=explanation,
                                     evidence_ids=[item.id for item in supported], source_finding_id=source_finding_id))
        self.recommend(code, action, explanation, supported, targets=targets, dependencies=dependencies, professional=professional)

    def finish(self, explanation, *, score=None, confidence="medium", next_step="Review the captured sources in the next revision."):
        if not self.recommendations:
            self.recommend("review_next_step", next_step, explanation, list(self.evidence.values())[:4],
                           professional=self.dimension == "risk_safety_flags")
        severity = max((item.severity for item in self.findings), key=_SEVERITY.get, default="info")
        return DimensionResult(dimension=self.dimension, state="evaluated" if score is not None else "not_evaluated", score=score,
            explanation=explanation, confidence=confidence if score is not None else "inconclusive",
            uncertainty=self.uncertainty, missing_inputs=self.missing, severity=severity, evidence=list(self.evidence.values()),
            findings=self.findings, recommendations=self.recommendations, evaluator=self.evaluator)


def _scene_proof(report):
    return evidence(report["ref"], "Phase 9 checked the scene as captured in the design version.",
                    {"configuration_fingerprint": report["configuration_fingerprint"], "measured_objects": len(report["measured_objects"]),
                     "rules": report["rules_executed"], "authored_dimensions_m": report["authored_dimensions_m"],
                     "findings_truncated": report["truncated"]}, origin="derived")


def _spatial_flag(assessment, report, item, *, professional=False):
    scene = report["ref"]
    refs = [ref({"object": "scene_object", "group": "scene_object", "zone": "scene_zone", "layer": "scene_layer"}[entry["type"]],
                entry["id"], scene.revision) for entry in item["entities"]]
    proof = evidence(scene, item["title"], {"finding_id": str(item["id"]), "rule": item["rule_id"],
                    "measurement": item["measurement"], "requirement_id": item["metadata"].get("requirement_id")}, origin="derived")
    dependencies = [scene, assessment.ctx.ref()]
    dependencies.extend(ref("bom", key, data["version"]) for key, data in assessment.ctx.state.get("boms", {}).items()
                        if data["scene_id"] == str(scene.id))
    assessment.flag(item["code"], item["explanation"], item["resolution"], [proof], severity="high" if item["severity"] == "error" else "review",
           source_finding_id=item["id"], targets=refs or [scene], dependencies=dependencies, professional=professional)


def _adverse(item):
    # Unsupported measurements and missing selectors are evidence gaps, even when Phase 9 labels the gap an error.
    code = item["code"]
    return item["severity"] != "info" and not any(word in code for word in ("unavailable", "unsupported", "missing"))


def _coverage(ctx, reports, required):
    return bool(reports) and len(reports) == len(ctx.state.get("scenes", {})) and all(
        report["measured_objects"] and required <= {item["id"] for item in report["rules_executed"] if item["complete"]}
        and ("clearance" not in required or all(report["clearance_scope"].values()))
        and not report["truncated"] for report in reports)


def spatial(ctx, reports, dimension):
    definitions = {
        "scale_proportion": ("measured_scene_envelope", {"venue_boundary", "geometry", "transforms"},
                             "The measured object envelopes fit the authored scene bounds under the captured rules."),
        "spatial_feasibility": ("configured_spatial_rules", _SPATIAL_RULES,
                                "The measured layout has no adverse finding in the reviewed configured geometry rules."),
        "furniture_decor": ("placed_furniture_and_decor", {"geometry", "venue_boundary", "collision", "zone_containment"},
                            "The placed furniture and decor satisfy the reviewed fit and collision checks."),
    }
    criterion, rules, positive = definitions[dimension]
    assessment = Assessment(ctx, dimension, criterion)
    covered = _coverage(ctx, reports, rules)
    assessment.uncertainty.append("This checks the captured placement and authored geometry. It does not assess styling, visual proportions, capacity, or engineering feasibility.")
    if dimension == "furniture_decor":
        ids = {key for scene in ctx.state.get("scenes", {}).values() for key, row in scene["objects"].items()
               if row.get("category", "").casefold() in {"furniture", "decor"} and row.get("status") == "active"}
        measured = {str(uid) for report in reports for uid in report["measured_objects"]}
        covered = covered and bool(ids) and ids <= measured
    else:
        ids = None
    for report in reports:
        assessment.add([_scene_proof(report)])
        for item in report["findings"]:
            relevant = ids is None or any(str(entry["id"]) in ids for entry in item["entities"])
            if relevant and item["rule_id"] in rules and _adverse(item):
                _spatial_flag(assessment, report, item)
    if assessment.findings:
        if not covered:
            assessment.uncertainty.append("Some scene or rule coverage remains incomplete. The listed issues are supported by the checks that ran.")
        return assessment.finish("The captured spatial checks found placement or geometry issues within this criterion.", score=1)
    if covered:
        return assessment.finish(positive, score=3, confidence="high")
    assessment.missing.append("measurable_layout_and_applicable_spatial_rules")
    return assessment.finish("The captured state does not provide complete scene and rule coverage for this criterion.",
                    next_step="Capture the measured scene, relevant placed objects, and enabled authored spatial checks.")


def accessibility(ctx, reports, clearance_ids):
    assessment = Assessment(ctx, "accessibility", "explicitly_selected_access_clearances")
    assessment.uncertainty.append("Only the selected authored clearances are reviewed. Route continuity, accessible facilities, local compliance, and operational access need separate review.")
    tested, unresolved = set(), set()
    for report in reports:
        scope = set(clearance_ids) & report["clearance_scope"].keys()
        if not scope:
            continue
        if "clearance" not in {item["id"] for item in report["rules_executed"]}:
            unresolved.update(scope)
            continue
        assessment.add([_scene_proof(report)])
        incomplete = {item["metadata"].get("requirement_id") for item in report["findings"]
                      if item["rule_id"] == "clearance" and item["code"] != "clearance_below_minimum"}
        covered = {key for key in scope - incomplete if report["clearance_scope"][key]} if not report["truncated"] else set()
        tested.update(covered)
        unresolved.update(scope - covered)
        for item in report["findings"]:
            if item["code"] == "clearance_below_minimum" and item["metadata"].get("requirement_id") in scope:
                _spatial_flag(assessment, report, item, professional=True)
    if assessment.findings:
        return assessment.finish("A measured separation is below a selected authored access clearance.", score=1, confidence="high")
    if clearance_ids and tested == set(clearance_ids) and not unresolved and len(reports) == len(ctx.state.get("scenes", {})):
        return assessment.finish("The checks found no shortfall for the selected authored access clearances.", score=3, confidence="high")
    assessment.missing.append("selected_authored_access_clearances_with_measurable_targets")
    return assessment.finish("The audit did not establish coverage for the requested access checks.",
                    next_step="Record applicable access targets and sourced clearances in Phase 9, then select their IDs in the audit.")


def _number(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def _basis_matches(ctx, bom):
    scene = ctx.state.get("scenes", {}).get(bom["scene_id"])
    return not bom["stale"] and (scene is None or scene["scene"]["current_revision"] == bom["scene_revision"])


def budget(ctx):
    assessment = Assessment(ctx, "budget_efficiency", "phase10_estimate_against_approved_ceiling")
    outcomes = []
    for key, source in ctx.state.get("budgets", {}).items():
        summary = source["summary"]
        variance = _number(summary.get("estimated_variance"))
        eligible = (source.get("latest") and not source.get("stale_reasons") and variance is not None
                    and summary.get("approved_amount") is not None and summary.get("estimated", {}).get("total") is not None)
        boms = ctx.state.get("boms", {})
        eligible = eligible and all(_basis_matches(ctx, boms[bom_id]) for bom_id in source.get("basis_bom_ids", []) if bom_id in boms)
        proof = evidence(ref("budget", key, source["version"], "summary"), "Phase 10 captured the budget totals and variance.",
                         {"currency": source["currency"], "estimated": summary.get("estimated", {}).get("total"),
                          "approved": summary.get("approved_amount"), "estimated_variance": summary.get("estimated_variance"),
                          "stale_reasons": source.get("stale_reasons", []), "eligible_for_comparison": bool(eligible)})
        assessment.add([proof])
        if not eligible:
            continue
        outcomes.append(variance > 0)
        if variance > 0:
            assessment.flag("estimate_exceeds_approved_budget", "The Phase 10 estimate exceeds the approved amount in its recorded currency.",
                   "Review priced cost drivers and approve a revised commercial package or value-engineering option.", [proof], severity="high",
                   dependencies=[ref("bom", bom_id) for bom_id in source.get("basis_bom_ids", [])] or [ctx.ref()])
    assessment.uncertainty.append("The criterion compares recorded costs with their approved ceiling. It provides no market-value or value-for-money judgment.")
    if outcomes:
        score = 2 if any(outcomes) and not all(outcomes) else 1 if any(outcomes) else 3
        return assessment.finish("Compared the eligible Phase 10 estimates with their own approved ceilings.", score=score, confidence="high")
    assessment.missing.append("current_complete_budget_estimate_and_approved_amount")
    return assessment.finish("A comparable approved budget and complete current estimate were unavailable.",
                    next_step="Capture a current approved budget with complete Phase 10 estimates in the design version.")


def production(ctx, *, technical=False):
    dimension = "technical_completeness" if technical else "production_complexity"
    criterion = "captured_technical_package" if technical else "captured_production_coordination"
    assessment = Assessment(ctx, dimension, criterion)
    eligible, supported = 0, 0
    for pin in ctx.pin_sources("production_record"):
        value = pin["data"]
        basis = value.get("readiness_basis")
        scene = ctx.state.get("scenes", {}).get(value.get("scene_id"))
        if basis and scene and basis["scene_revision"] != scene["scene"]["current_revision"]:
            assessment.uncertainty.append("A production review references a different scene revision from the captured design scene.")
            continue
        proof = evidence(ref("production_record", pin["id"], pin["version"]), "The presentation pinned the production readiness evidence.",
                         {"status": value.get("status"), "readiness_basis": basis, "blockers": value.get("readiness_blockers", []),
                          "procurement_status_counts": value.get("procurement_status_counts", {})})
        assessment.add([proof])
        if pin["stale"] or value.get("readiness_stale"):
            continue
        if basis or value.get("readiness_blockers"):
            eligible += 1
        if basis and basis.get("specifications") and not value.get("readiness_blockers"):
            supported += 1
        elif value.get("readiness_blockers") and not technical:
            assessment.flag("production_readiness_blockers", "The captured work package has unresolved production readiness requirements.",
                   "Resolve the Phase 10 package and task dependencies with the production lead.", [proof])
    if technical:
        for key, bom in ctx.state.get("boms", {}).items():
            if not _basis_matches(ctx, bom):
                assessment.uncertainty.append("A captured BOM is stale or uses a different scene revision.")
                continue
            estimates = {line_id: item for line_id, item in bom["lines"].items() if item["basis"] == "estimated"}
            if not estimates:
                continue
            eligible += 1
            proof = evidence(ref("bom", key, bom["version"]), "The audit reviewed the captured estimated BOM lines and attached technical evidence.",
                             {"approved": bool(bom["approved_at"]), "estimated_line_count": len(estimates)})
            assessment.add([proof])
            broken = False
            for line_id, item in estimates.items():
                if item["total"] is None or item.get("issues"):
                    broken = True
                    if len(assessment.findings) < 30:
                        line = evidence(ref("bom_line", line_id), "The captured estimate records incomplete line inputs.",
                                        {"issues": item.get("issues", []), "price_recorded": item["total"] is not None})
                        assessment.flag("incomplete_bom_line", "A captured estimated line lacks completed input or pricing checks.",
                               "Resolve the referenced Phase 10 line inputs before technical approval.", [line], dependencies=[proof.source])
                spec = item.get("dependencies", {}).get("specification", {})
                if spec and len(assessment.evidence) < 80:
                    assessment.add([evidence(ref("technical_specification", spec["id"], spec["version"]), "The BOM references this captured technical sheet.",
                                    {"status": spec["status"], "stale": spec["stale"]})])
            scoped_sheets = all(item.get("dependencies", {}).get("specification") for item in estimates.values())
            if bom["approved_at"] and scoped_sheets and not broken:
                supported += 1
        for checkpoint in ctx.checkpoints:
            assessment.add([evidence(ref("approval_checkpoint", checkpoint["id"], checkpoint["revision"]),
                            "This is the Part 1 checkpoint state recorded at audit start.", {"status": checkpoint["status"]})])
    assessment.uncertainty.append("This criterion reviews recorded package evidence and gaps. Labour hours, structural systems, and execution risk require separate evidence.")
    if assessment.findings or supported:
        score = 2 if supported and (assessment.findings or supported < eligible) else 3 if supported else 1
        return assessment.finish("Reviewed the captured technical package and BOM inputs." if technical else
                        "Reviewed available production coordination and readiness evidence.", score=score)
    assessment.missing.append("captured_current_boq_and_technical_sheets" if technical else "captured_production_readiness_and_dependencies")
    return assessment.finish("The available records do not establish sufficient coverage for this criterion.",
                    next_step="Pin a current Phase 10 package with technical sheets, BOM inputs, and production review evidence.")


def sustainability(ctx):
    assessment = Assessment(ctx, "sustainability", "verified_reuse_requirements")
    checks = []
    for key, item in ctx.state.get("memory", {}).items():
        authored = item.get("content", {}).get("sustainability")
        if not (isinstance(authored, dict) and authored.get("reuse_required") is True and item.get("verification") == "verified"
                and item.get("source_reference") and type(authored.get("reuse_verified")) is bool):
            continue
        checked = authored["reuse_verified"]
        proof = evidence(ref("memory_item", key, item["version"], "content.sustainability"), "Recorded verified project reuse evidence.",
                         {"source_reference": item["source_reference"], "reuse_required": True, "reuse_verified": checked})
        assessment.add([proof])
        checks.append(checked)
        if not checked:
            assessment.flag("documented_reuse_requirement_unmet", "The verified memory records that its reuse requirement is unmet.",
                   "Review reuse options and record the updated supplier or material evidence.", [proof])
    assessment.uncertainty.append("This criterion covers documented reuse only. It does not estimate emissions, material recovery, or actual waste.")
    if checks:
        return assessment.finish("Reviewed the captured verified reuse requirements and their recorded outcomes.",
                        score=3 if all(checks) else 2 if any(checks) else 1)
    assessment.missing.append("verified_sourced_sustainability_criterion_and_outcome")
    return assessment.finish("The captured state has no verified evidence for a sustainability outcome.",
                    next_step="Record applicable reuse criteria and their sourced outcomes in verified Project Memory.")


def led(ctx):
    assessment = Assessment(ctx, "led_suitability", "phase10_captured_led_pixel_grid")
    count = 0
    for pin in ctx.pin_sources("led_quantity"):
        data = pin["data"]
        scene = ctx.state.get("scenes", {}).get(data.get("scene_id"))
        if not scene or scene["scene"]["current_revision"] != data.get("scene_revision") or pin["stale"] or not pin["ready"]:
            assessment.uncertainty.append("A pinned LED quantity lacks matching captured scene geometry or a valid current calculation.")
            continue
        outputs = data.get("outputs", {})
        fields = ("width_px", "height_px", "total_pixels", "pixel_pitch_m")
        if not all((_number(outputs.get(field)) or 0) > 0 for field in fields):
            continue
        count += 1
        assessment.add([evidence(ref("quantity", pin["id"], pin["version"], "outputs"), "Phase 10 recorded a pixel grid for this captured LED surface.",
                        {**{field: outputs[field] for field in fields}, "scene_id": data["scene_id"],
                         "scene_revision": data["scene_revision"], "source_object_id": data.get("source", {}).get("target_id")}, origin="derived")])
    assessment.uncertainty.append("Pixel-grid suitability covers recorded dimensions and pitch. Viewing distance, power, rigging, and load capacity require separate review.")
    if count:
        return assessment.finish("The captured Phase 10 LED takeoff supports a pixel grid for the reviewed surface.", score=3, confidence="high")
    assessment.missing.append("valid_led_takeoff_pinned_to_captured_scene")
    return assessment.finish("A valid LED takeoff could not be matched to the captured scene.",
                    next_step="Capture the corresponding scene and a presentation revision that pins the Phase 10 LED calculation.")


def safety(ctx, reports):
    assessment = Assessment(ctx, "risk_safety_flags", "preliminary_risk_and_missing_inputs", method="preliminary_screening")
    assessment.uncertainty.append(SAFETY_SCOPE)
    for report in reports:
        assessment.add([_scene_proof(report)])
        for item in report["findings"]:
            if item["rule_id"] in {"venue_boundary", "collision", "clearance"} and _adverse(item):
                _spatial_flag(assessment, report, item, professional=True)
    candidates = []
    for scene in ctx.state.get("scenes", {}).values():
        revision = scene["scene"]["current_revision"]
        for key, item in scene["objects"].items():
            if item.get("node_kind") == "object" and item.get("status") != "archived" and (
                item.get("category", "").casefold() in _RISK_OBJECTS or item.get("object_type", "").casefold() in _RISK_OBJECTS
            ):
                candidates.append(evidence(ref("scene_object", key, revision), "A captured object warrants scope-specific professional review.",
                                           {"category": item["category"], "object_type": item["object_type"]}))
    if candidates:
        assessment.flag("professional_review_required", "Captured production or installation objects can require specialist review.",
               "Have qualified professionals verify applicable exposure, anchorage, rigging, electrical, load, venue and inspection requirements.",
               candidates[:20], professional=True)
    constraints = ctx.state.get("context", {}).get("project", {}).get("constraints") or {}
    project = ctx.state.get("context", {}).get("project", {})
    if isinstance(constraints, dict) and constraints.get("safety"):
        assessment.add([evidence(ref("project", project["id"], project["version"], "constraints.safety"),
                        "The captured project includes authored safety constraints.", {"constraints": constraints["safety"]})])
    for key, item in ctx.state.get("memory", {}).items():
        if item.get("category") == "safety_constraints":
            assessment.add([evidence(ref("memory_item", key, item["version"], "content"), "Captured safety constraints with their recorded attribution.",
                            {"content": item["content"], "verification": item["verification"], "source_reference": item["source_reference"]})])
    assessment.missing.append("professionally_verified_site_specific_safety_package")
    if not candidates and not reports:
        assessment.missing.append("captured_measured_site_and_installation_geometry")
    return assessment.finish("Risk screening is inconclusive. Retain authored constraints and resolve the preliminary findings through qualified review.",
                    next_step="Collect the applicable engineer, venue and inspection evidence for qualified review.")
