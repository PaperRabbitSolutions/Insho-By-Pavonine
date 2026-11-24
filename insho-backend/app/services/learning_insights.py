"""Count repeated observations across independently scoped events."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from typing import Literal
from uuid import UUID, uuid5

from fastapi import HTTPException
from pydantic import Field, ValidationError

from app.models.learning import LearningActual
from app.schemas.ai import AIExecutionRequest
from app.schemas.learning import Input, Insight, Source
from app.services import design_snapshot
from app.services.ai_orchestration import ai_execution_service
from app.services.learning_actuals import active_actuals
from app.services.learning_reconciliation import MAX_INPUTS
from app.services.learning_sources import distinct, invalid, reference


_NAMESPACE = UUID("a3484d2c-3dcb-4923-a2a2-35120625887b")
MIN_EVENTS = 2
MAX_AI_INSIGHTS = 20
_QUANTITIES = {"cost": "Review current rates and scope before setting the next estimate.",
               "quantity": "Review takeoff assumptions against the verified consumption records.",
               "labour": "Review team-hour assumptions before scheduling or pricing the next package.",
               "installation": "Review the planned duration and site dependencies on similar installation tasks.",
               "wastage": "Review material allowances and the recorded waste collection method."}
_FUTURE_DECISIONS = {"cost": "Cost estimate", "quantity": "Material takeoff", "labour": "Labour plan", "installation": "Installation schedule",
                     "wastage": "Material allowance", "vendor_delivery": "Supplier delivery terms", "delay": "Schedule contingency",
                     "change_order": "Scope and change control", "concept_decision": "Creative direction",
                     "weather_incident": "Weather contingency", "production_incident": "Production review"}
_QUALITATIVE = {
    "vendor_delivery": ("Recorded vendor delivery miss", "Review supplier delivery commitments and the event-specific evidence before purchasing."),
    "delay": ("Recorded activity delay", "Review schedule dependencies and verified site records before assigning contingency."),
    "change_order": ("Repeated source-attributed change-order category", "Review the original scope and approved change history during future scope planning."),
    "concept_decision": ("Repeated final creative decision of this type", "Review the exact briefs and decision records before proposing a related concept."),
    "weather_incident": ("Repeated recorded weather incident category", "Review site-specific contingency requirements with the event team."),
    "production_incident": ("Repeated recorded production incident category", "Review production controls and the event-specific incident records."),
}


@dataclass(frozen=True)
class Observation:
    record: object
    label: str
    sources: list


def _actual_sources(row):
    sources = [reference("actual", row.id, row.revision)]
    sources.extend(Source.model_validate(item) for item in row.sources if item["kind"] != "asset")
    return sources


def _group(row):
    data = row.data
    kind = row.kind
    if kind == "vendor" and data["dimension"] in {"delivery_timing", "delivery_reliability"}:
        label = "missed" if data["outcome"] == "missed" else data["outcome"]
        return ("vendor_delivery", data["supplier_id"] + "/" + data["dimension"]), label
    if kind == "delay":
        duration = row.derived.get("completion_delay_seconds")
        if duration is not None:
            return (kind, "completion"), "late" if Decimal(duration) > 0 else "on_time"
    if kind == "change_order" and data["approval_status"] == "approved" and data.get("reason_category"):
        return (kind, "approved_orders"), data["reason_category"] if row.derived.get("reason_source_status") == "attributed" else "unattributed"
    if kind == "concept_decision":
        return (kind, "final_checkpoints"), row.derived["decision"]
    if kind in {"weather_incident", "production_incident"}:
        return (kind, "reported_categories"), data["category"].strip().casefold()
    return None


def _quantitative_groups(reports, by_id):
    result = defaultdict(list)
    skipped = False
    for report in reports:
        for comparison in report.content["comparisons"]:
            if comparison["status"] != "compared":
                continue
            row = by_id.get(comparison["verified_actual_id"])
            if row is None:
                skipped = True
                continue
            metric, change = comparison["metric"], Decimal(comparison["difference"])
            group = (metric, "/".join((comparison["category"], comparison["comparison_group"], comparison["estimate_unit"])))
            label = "above_estimate" if change > 0 else "below_estimate" if change < 0 else "matches_estimate"
            result[group].append(Observation(row, label, [
                reference("reconciliation", report.id), *_actual_sources(row),
            ]))
    return result, skipped


def _signals(kind, items):
    if kind in _QUANTITIES:
        return {"above_estimate", "below_estimate"}
    if kind == "vendor_delivery":
        return {"missed"}
    if kind == "delay":
        return {"late"}
    return {item.label for item in items} - {"unattributed"}


def _insight(project, group, label, items, created_at):
    by_event = defaultdict(list)
    for item in items:
        by_event[item.record.event_id].append(item)
    kind, context = group
    if kind in _QUANTITIES or kind == "vendor_delivery":
        # Contradictory records from the same event cannot count as support for a consistent direction.
        eligible_support = [event_items for event_items in by_event.values() if {item.label for item in event_items} == {label}]
    else:
        eligible_support = [[item for item in event_items if item.label == label] for event_items in by_event.values()]
    support = [item for event_items in eligible_support for item in event_items]
    events = sorted({item.record.event_id for item in support})
    if len(events) < MIN_EVENTS:
        return None
    eligible = len(by_event)
    if kind in _QUANTITIES:
        observation = f"Verified {kind} actuals were {label.replace('_', ' ')} in {len(events)} events in this comparison group."
        action = _QUANTITIES[kind]
    else:
        wording, action = _QUALITATIVE[kind]
        observation = f"{wording} recorded in {len(events)} events. Outcome category is {label}."
    dates = [item.record.occurred_on for item in items]
    sources = distinct(item for sample in support for item in sample.sources)
    key = design_snapshot.digest({"project": str(project.id), "group": group, "signal": label,
                                  "sources": [item.model_dump(mode="json") for item in sources]})
    return Insight(id=uuid5(_NAMESPACE, key), project_id=project.id, created_at=created_at, category=kind, context=context, sample_count=len(events),
                   eligible_event_count=eligible, period_start=min(dates), period_end=max(dates), event_ids=events, sources=sources,
                   method="Counts distinct verified events with the same recorded outcome within this group.",
                   coverage=f"{len(events)} of {eligible} events with eligible records in this group support this observation.",
                   confidence="moderate" if len(events) >= 5 and len(events) == eligible else "limited",
                   observation=observation, recommended_action=action, future_decision=_FUTURE_DECISIONS[kind],
                   uncertainty="The reviewed event set is selected and may be incomplete. A repeated observation does not establish causation or predict a future event.")


def build(db, project, events, reports):
    rows = db.scalars(active_actuals(project, events).where(LearningActual.status == "verified")
                      .order_by(LearningActual.event_id, LearningActual.id).limit(MAX_INPUTS + 1)).all()
    if len(rows) > MAX_INPUTS:
        invalid("The selected events exceed the verified learning input limit.")
    groups, skipped = _quantitative_groups(reports, {str(row.id): row for row in rows})
    for row in rows:
        identified = _group(row)
        if identified is not None:
            group, label = identified
            groups[group].append(Observation(row, label, _actual_sources(row)))
    created_at = datetime.now(timezone.utc)
    insights = [
        insight for group, records in sorted(groups.items()) for label in sorted(_signals(group[0], records))
        if (insight := _insight(project, group, label, records, created_at)) is not None
    ]
    warnings = []
    if skipped:
        warnings.append("reconciliation_contains_superseded_or_unavailable_actual")
    if not insights:
        warnings.append("insufficient_repeated_verified_observations")
    evidence = distinct(source for item in insights for source in item.sources)
    fingerprint = design_snapshot.digest({"events": list(map(str, events)), "reports": [str(row.id) for row in reports],
        "actual_revisions": [(str(row.id), row.revision) for row in rows]})
    return insights, evidence, warnings, fingerprint


class AIEmphasis(Input):
    insight_id: UUID
    evidence_id: UUID
    emphasis: Literal["review_estimate", "review_execution", "review_evidence"]


class AIOutput(Input):
    reviews: list[AIEmphasis] = Field(max_length=MAX_AI_INSIGHTS)


async def interpret(db, project, user, request_id, reports_key, insights):
    selected = {item.id: {source.id for source in item.sources if source.kind == "actual"} for item in insights[:MAX_AI_INSIGHTS]}
    if not selected:
        return insights, None, ["ai_no_eligible_insights"]
    # Only server-generated categories, counts and opaque evidence identifiers are shared.
    payload = [{"insight_id": str(item.id), "category": item.category, "sample_count": item.sample_count,
                "eligible_event_count": item.eligible_event_count, "evidence_ids": sorted(map(str, selected[item.id]))[:20]}
               for item in insights if item.id in selected]
    sent = {UUID(item["insight_id"]): {UUID(key) for key in item["evidence_ids"]} for item in payload}
    execution = None
    try:
        execution = await ai_execution_service.execute(db, AIExecutionRequest(
            task="learning_insight_review", feature="insho_learning", project_id=project.id, workspace_id=project.workspace_id,
            request_id=request_id, idempotency_key=reports_key, input=json.dumps(payload), output_format="json",
            output_schema=AIOutput.model_json_schema(), required_capabilities=["text", "structured_output"], timeout_seconds=30,
            instructions=("Choose a review emphasis for each eligible historical observation. Cite an evidence ID supplied for that insight. "
                          "Return the requested structured schema. Never add factual claims, conclusions, estimates, or actions."),
        ), user, provenance={"source": "learning_insights"})
        if not execution.success or not execution.output:
            return insights, execution.execution_id, ["ai_review_unavailable"]
        response = AIOutput.model_validate_json(execution.output)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return insights, detail.get("execution_id"), ["ai_review_unavailable"]
    except ValidationError:
        return insights, execution.execution_id if execution is not None else None, ["ai_review_output_invalid"]
    accepted, rejected = {}, set()
    for review in response.reviews:
        if review.insight_id in accepted or review.evidence_id not in sent.get(review.insight_id, set()):
            accepted.pop(review.insight_id, None)
            rejected.add(review.insight_id)
        elif review.insight_id not in rejected:
            accepted[review.insight_id] = review
    parsed = [item.model_copy(update={"ai_emphasis": accepted[item.id].emphasis, "ai_origin": "ai_inferred",
                                      "ai_evidence_id": accepted[item.id].evidence_id})
              if item.id in accepted else item for item in insights]
    return parsed, execution.execution_id, ["ai_reviews_incomplete"] if set(accepted) != set(sent) or rejected else []
