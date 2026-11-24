"""Versioned organization materials and production-record assignments."""

from uuid import uuid4

from sqlalchemy import select

from app.models.production import ProductionMaterial
from app.models.production_bom import MaterialDefinition, MaterialDefinitionRevision
from app.security import require_membership, require_project_access
from app.services import production
from app.services.production_costing import decimal_text
from app.services.spatial import _missing


def _definition_statement(organization_id):
    return select(MaterialDefinition).where(MaterialDefinition.organization_id == organization_id)


def _definition(db, organization_id, definition_id, *, write=False, lock=False):
    statement = _definition_statement(organization_id).where(MaterialDefinition.id == definition_id)
    if lock:
        statement = statement.with_for_update(read=not write)
    row = db.scalar(statement.execution_options(populate_existing=True))
    if row is None:
        _missing()
    return row


def _revision(db, definition, version=None):
    row = db.get(MaterialDefinitionRevision, (definition.id, version or definition.current_version))
    if row is None:
        _missing()
    return row


def _definition_view(row, revision):
    return {
        "id": row.id, "organization_id": row.organization_id, "version": revision.version,
        "current_version": row.current_version, "name": revision.name, "category": revision.category,
        "unit": revision.unit, "status": revision.status, "unit_rate": revision.unit_rate, "currency": revision.currency,
        "allowance_percent": revision.allowance_percent, **revision.details,
        "created_at": row.created_at, "updated_at": row.updated_at, "revised_at": revision.created_at,
        "entered_by_user_id": revision.entered_by_user_id,
    }


def save_definition(db, organization_id, user_id, data, *, definition_id=None):
    require_membership(db, user_id, organization_id, write=True)
    if definition_id is None:
        definition = MaterialDefinition(id=uuid4(), organization_id=organization_id, current_version=1)
        db.add(definition)
        db.flush()
    else:
        definition = _definition(db, organization_id, definition_id, write=True, lock=True)
        if data.expected_version != definition.current_version:
            production._conflict("material_version_conflict", "Reload the material definition.",
                                 current_version=definition.current_version)
        definition.current_version += 1
    revision = MaterialDefinitionRevision(
        definition_id=definition.id, version=definition.current_version, name=data.name.strip(),
        category=data.category.strip(), unit=data.unit, status=data.status, unit_rate=data.unit_rate,
        currency=data.currency.upper() if data.currency else None, allowance_percent=data.allowance_percent,
        details={field: getattr(data, field) for field in (
            "description", "specification", "finish", "supplier_reference", "source_reference",
        )}, entered_by_user_id=user_id,
    )
    db.add(revision)
    db.flush()
    response = _definition_view(definition, revision)
    production._commit_changes(db)
    return response


def get_definition(db, organization_id, definition_id, user_id, *, version=None):
    require_membership(db, user_id, organization_id)
    definition = _definition(db, organization_id, definition_id)
    return _definition_view(definition, _revision(db, definition, version))


def list_definitions(db, organization_id, user_id, *, status, offset, limit):
    require_membership(db, user_id, organization_id)
    query = (select(MaterialDefinition, MaterialDefinitionRevision)
             .join(MaterialDefinitionRevision, (MaterialDefinitionRevision.definition_id == MaterialDefinition.id)
                   & (MaterialDefinitionRevision.version == MaterialDefinition.current_version))
             .where(MaterialDefinition.organization_id == organization_id))
    if status is not None:
        query = query.where(MaterialDefinitionRevision.status == status)
    rows = db.execute(query.order_by(MaterialDefinitionRevision.name, MaterialDefinition.id).offset(offset).limit(limit))
    return [_definition_view(definition, revision) for definition, revision in rows]


def assignment_values(db, scene, user_id, data, row):
    definition_id = data.definition_id if "definition_id" in data.model_fields_set else row.definition_id
    overrides = row.overrides or {}
    if definition_id != row.definition_id and overrides and "overrides" not in data.model_fields_set:
        production._invalid("Supply the overrides to use with the new material definition.")
    if "overrides" in data.model_fields_set:
        overrides = {}
        if data.overrides is not None:
            overrides = data.overrides.model_dump(mode="json", exclude_unset=True)
            for field in ("unit_rate", "allowance_percent"):
                amount = getattr(data.overrides, field)
                if amount is not None:
                    overrides[field] = decimal_text(amount)
            if overrides.get("allowance_percent") is None:
                overrides.pop("allowance_percent", None)
            if overrides.get("currency"):
                overrides["currency"] = overrides["currency"].upper()
            if "unit_rate" in overrides:
                overrides.setdefault("currency", None)
    if definition_id is None and overrides:
        production._invalid("Link a material definition before adding project overrides.")
    if definition_id is not None:
        project = require_project_access(db, user_id, scene.project_id)
        current = _definition(db, project.organization_id, definition_id, lock=True)
        revision = _revision(db, current)
        if row.definition_id != definition_id and revision.status == "archived":
            production._invalid("The material definition is archived.")
        if "overrides" in data.model_fields_set and overrides.get("unit_rate") is not None:
            overrides["rate_unit"] = revision.unit
    return definition_id, overrides


class MaterialResolver:
    def __init__(self, db, organization_id, *, lock=False):
        self.db = db
        self.organization_id = organization_id
        self.lock = lock
        self.revisions = {}

    def effective(self, usage):
        if usage is None or usage.definition_id is None:
            return None
        definition_id = usage.definition_id
        if definition_id not in self.revisions:
            query = _definition_statement(self.organization_id).where(MaterialDefinition.id == definition_id)
            if self.lock:
                query = query.with_for_update(read=True)
            definition = self.db.scalar(query.execution_options(populate_existing=True))
            self.revisions[definition_id] = _revision(self.db, definition) if definition is not None else None
        revision = self.revisions[definition_id]
        if revision is None:
            return None
        values = {
            "id": str(usage.id), "version": usage.version, "name": usage.name, "description": usage.description,
            "definition_id": str(definition_id), "definition_version": revision.version,
            "definition_name": revision.name, "category": revision.category, "unit": revision.unit,
            "status": revision.status, "currency": revision.currency,
            "unit_rate": decimal_text(revision.unit_rate) if revision.unit_rate is not None else None,
            "allowance_percent": decimal_text(revision.allowance_percent),
            **{key: value for key, value in revision.details.items() if key != "description"},
            **{key: value for key, value in usage.overrides.items() if key != "rate_unit"},
        }
        override_unit = usage.overrides.get("rate_unit")
        if override_unit is not None and override_unit != revision.unit:
            values.update(unit_rate=None, currency=None, override_rate_unit=override_unit)
        return values


def get_assignment(db, scene_id, record_id, material_id, user_id):
    scene = production._scene(db, scene_id, user_id)
    record = production._record(db, scene, record_id)
    usage = production._scoped_row(db, ProductionMaterial, material_id, ProductionMaterial.record_id == record.id)
    project = require_project_access(db, user_id, scene.project_id)
    return {"id": usage.id, "name": usage.name, "description": usage.description, "version": usage.version,
            "definition_id": usage.definition_id,
            "overrides": {key: value for key, value in usage.overrides.items() if key != "rate_unit"},
            "effective": MaterialResolver(db, project.organization_id).effective(usage)}
