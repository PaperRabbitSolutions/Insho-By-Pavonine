"""Organization supplier identities and project sourcing relationships."""

from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from app.models.object_library import ObjectDefinition
from app.models.production_procurement import ProductionProjectSupplier, ProductionSupplier, ProductionSupplierOffering
from app.security import require_membership
from app.services import procurement_scope as scope, production
from app.services.production_costing import convert_quantity
from app.services.production_materials import _definition as material_definition, _revision
from app.services.spatial import _missing


def supplier_for_org(db, organization_id, supplier_id, *, lock=False, write=False):
    query = select(ProductionSupplier).where(
        ProductionSupplier.id == supplier_id, ProductionSupplier.organization_id == organization_id,
    )
    if lock:
        query = query.with_for_update(read=not write).execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        _missing()
    return row


def supplier_snapshot(row):
    return {"id": str(row.id), "name": row.name, "version": row.version, "status": row.status, "details": row.details}


def supplier_for_project(db, project, supplier_id, *, lock=False):
    row = supplier_for_org(db, project.organization_id, supplier_id, lock=lock)
    link = db.get(ProductionProjectSupplier, (project.id, supplier_id))
    if row.status != "active" or (link is not None and not link.active):
        production._conflict("supplier_unavailable", "The supplier is inactive for this project.")
    return row


def available_for_project(db, project, supplier_id):
    supplier = db.get(ProductionSupplier, supplier_id)
    if supplier is None or supplier.organization_id != project.organization_id or supplier.status != "active":
        return False
    link = db.get(ProductionProjectSupplier, (project.id, supplier_id))
    return link is None or link.active


def supplier_view(row):
    return {**supplier_snapshot(row), "organization_id": row.organization_id, "created_at": row.created_at,
            "updated_at": row.updated_at, "last_edited_by_user_id": row.last_edited_by_user_id}


def save_supplier(db, organization_id, user_id, data, supplier_id=None):
    require_membership(db, user_id, organization_id, write=True)
    if supplier_id is None:
        row = ProductionSupplier(id=uuid4(), organization_id=organization_id, version=1)
        db.add(row)
    else:
        row = supplier_for_org(db, organization_id, supplier_id, lock=True, write=True)
        scope.check_version(data.expected_version, row.version, "supplier")
        row.version += 1
    row.name, row.status = data.name.strip(), data.status
    row.details = data.model_dump(mode="json", exclude={"name", "status", "expected_version"})
    row.last_edited_by_user_id = user_id
    scope.flush(db)
    result = supplier_view(row)
    production._commit_changes(db)
    return result


def get_supplier(db, organization_id, supplier_id, user_id):
    require_membership(db, user_id, organization_id)
    return supplier_view(supplier_for_org(db, organization_id, supplier_id))


def list_suppliers(db, organization_id, user_id, *, status, offset, limit):
    require_membership(db, user_id, organization_id)
    query = select(ProductionSupplier).where(ProductionSupplier.organization_id == organization_id)
    if status is not None:
        query = query.where(ProductionSupplier.status == status)
    return [supplier_view(row) for row in db.scalars(query.order_by(
        ProductionSupplier.name, ProductionSupplier.id,
    ).offset(offset).limit(limit))]


def offering_for_supplier(db, supplier_id, offering_id, *, lock=False):
    query = select(ProductionSupplierOffering).where(
        ProductionSupplierOffering.id == offering_id, ProductionSupplierOffering.supplier_id == supplier_id,
    )
    if lock:
        query = query.with_for_update(read=True).execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        _missing()
    return row


def offering_snapshot(row):
    return {"id": str(row.id), "supplier_id": str(row.supplier_id), "name": row.name, "kind": row.kind,
            "version": row.version, "active": row.active, "material_id": str(row.material_id) if row.material_id else None,
            "definition_id": str(row.definition_id) if row.definition_id else None, "details": row.details}


def _offering_view(row):
    return {**offering_snapshot(row), "created_at": row.created_at, "updated_at": row.updated_at,
            "last_edited_by_user_id": row.last_edited_by_user_id}


def material_reference(db, organization_id, material_id, *, lock=False):
    definition = material_definition(db, organization_id, material_id, lock=lock)
    revision = _revision(db, definition)
    return {"id": str(definition.id), "name": revision.name, "version": revision.version, "status": revision.status,
            "unit": revision.unit, "specification": revision.details.get("specification"),
            "finish": revision.details.get("finish")}


def component_reference(db, organization_id, definition_id, *, project=None, lock=False):
    query = select(ObjectDefinition).where(ObjectDefinition.organization_id == organization_id,
                                           ObjectDefinition.id == definition_id)
    if lock:
        query = query.with_for_update(read=True).execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        _missing()
    if ((row.project_id is not None and (project is None or row.project_id != project.id))
            or (row.workspace_id is not None and (project is None or row.workspace_id != project.workspace_id))):
        _missing()
    return {"id": str(row.id), "name": row.name, "version": row.version, "status": row.status}


def save_offering(db, organization_id, supplier_id, user_id, data, offering_id=None):
    require_membership(db, user_id, organization_id, write=True)
    supplier_for_org(db, organization_id, supplier_id, lock=True, write=True)
    if data.material_id is not None:
        material = material_reference(db, organization_id, data.material_id, lock=True)
        if material["unit"] != data.unit and data.unit is not None:
            try:
                convert_quantity(Decimal(1), data.unit, material["unit"])
            except ValueError:
                production._invalid("The offering and catalogue material units are incompatible.")
    if data.definition_id is not None:
        component_reference(db, organization_id, data.definition_id, lock=True)
    if offering_id is None:
        row = ProductionSupplierOffering(id=uuid4(), supplier_id=supplier_id, version=1)
        db.add(row)
    else:
        row = offering_for_supplier(db, supplier_id, offering_id)
        scope.check_version(data.expected_version, row.version, "offering")
        row.version += 1
    row.name, row.kind, row.active = data.name.strip(), data.kind, data.active
    row.material_id, row.definition_id = data.material_id, data.definition_id
    row.details = data.model_dump(mode="json", exclude={"name", "kind", "active", "material_id", "definition_id", "expected_version"})
    row.last_edited_by_user_id = user_id
    scope.flush(db)
    result = _offering_view(row)
    production._commit_changes(db)
    return result


def list_offerings(db, organization_id, supplier_id, user_id, *, offset, limit):
    require_membership(db, user_id, organization_id)
    supplier_for_org(db, organization_id, supplier_id)
    rows = db.scalars(select(ProductionSupplierOffering).where(ProductionSupplierOffering.supplier_id == supplier_id)
                      .order_by(ProductionSupplierOffering.name, ProductionSupplierOffering.id).offset(offset).limit(limit))
    return [_offering_view(row) for row in rows]


def _project_view(link, supplier):
    return {"project_id": link.project_id, "supplier_id": link.supplier_id, "version": link.version,
            "active": link.active, "note": link.note, "history": link.history, "supplier": supplier_snapshot(supplier)}


def link_supplier(db, project_id, supplier_id, user_id, data):
    project = scope.project_scope(db, project_id, user_id, write=True)
    supplier = supplier_for_org(db, project.organization_id, supplier_id, lock=True)
    if data.active and supplier.status != "active":
        production._conflict("supplier_unavailable", "Activate the supplier before adding it to the project.")
    row = db.get(ProductionProjectSupplier, (project.id, supplier.id))
    scope.check_version(data.expected_version, row.version if row is not None else 0, "project_supplier")
    if row is None:
        row = ProductionProjectSupplier(project_id=project.id, supplier_id=supplier.id, version=0, history=[])
        db.add(row)
    row.version += 1
    row.active, row.note = data.active, data.note
    row.history = [*row.history, scope.audit(user_id, "linked" if row.active else "deactivated", note=row.note,
                                            supplier=supplier_snapshot(supplier), version=row.version)]
    scope.flush(db)
    result = _project_view(row, supplier)
    production._commit_changes(db)
    return result


def list_project_suppliers(db, project_id, user_id, *, offset, limit):
    project = scope.project_scope(db, project_id, user_id)
    rows = db.execute(select(ProductionProjectSupplier, ProductionSupplier)
                      .join(ProductionSupplier, ProductionSupplier.id == ProductionProjectSupplier.supplier_id)
                      .where(ProductionProjectSupplier.project_id == project.id,
                             ProductionSupplier.organization_id == project.organization_id)
                      .order_by(ProductionSupplier.name, ProductionSupplier.id).offset(offset).limit(limit))
    return [_project_view(link, supplier) for link, supplier in rows]
