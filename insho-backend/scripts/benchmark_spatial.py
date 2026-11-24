"""Measure spatial API requests against a disposable local PostgreSQL database."""

from contextlib import contextmanager
import os
from statistics import median
from time import perf_counter
import tracemalloc
from uuid import uuid4

from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.auth import create_session
from app.db.base import Base
from app.db.database import get_db
from app.main import app
from app.models.object_library import ObjectDefinition
from app.models.organization import Organization
from app.models.organization_member import OrganizationMember
from app.models.project import Project
from app.models.spatial import SpatialObject, SpatialScene, SpatialZone
from app.models.user import User
from app.models.workspace import Workspace


@contextmanager
def isolated_database():
    load_dotenv(".env")
    source = make_url(os.environ["TEST_DATABASE_URL"])
    if source.host not in {"localhost", "127.0.0.1"} or source.database != "insho_test_db":
        raise RuntimeError("Use a localhost TEST_DATABASE_URL for insho_test_db.")
    name = "insho_test_db_spatial_bench_" + uuid4().hex[:10]
    admin = create_engine(source, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(source.set(database=name))
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()


def seed_project(db):
    org = Organization(name="Spatial Benchmark", slug="spatial-benchmark")
    user = User(full_name="Benchmark", email="benchmark@example.test", password_hash="test-session-only")
    db.add_all([org, user])
    db.flush()
    workspace = Workspace(organization_id=org.id, name="Benchmark Workspace", slug="benchmark")
    db.add_all([workspace, OrganizationMember(user_id=user.id, organization_id=org.id, role="owner")])
    db.flush()
    project = Project(organization_id=org.id, workspace_id=workspace.id, name="Scalability")
    db.add(project)
    db.flush()
    db.add_all([
        ObjectDefinition(organization_id=org.id, name=f"Chair {i:03d}", category="furniture", object_type="chair",
                         width_m=1, depth_m=1, height_m=1, geometry={"type": "box"}, default_transform={},
                         plan_compatible=True)
        for i in range(600)
    ])
    token, _ = create_session(db, user)
    return token, org.id, project.id


def seed_scene(db, project_id, count, spacing):
    scene = SpatialScene(project_id=project_id, name=f"Benchmark {count}", width_m=90, depth_m=90, height_m=8)
    db.add(scene)
    db.flush()
    parent = None
    for index in range(12):
        group = SpatialObject(scene_id=scene.id, name=f"Group {index}", object_type="group", category="group",
                              node_kind="group", parent_object_id=parent, order_index=index)
        db.add(group)
        db.flush()
        parent = group.id
    db.add_all([
        SpatialZone(scene_id=scene.id, name=f"Zone {index}", category="area", width_m=5, depth_m=5, height_m=1,
                    position_x_m=5 + 7 * index, position_y_m=80, position_z_m=2, order_index=index)
        for index in range(8)
    ])
    polygon = [{"x_m": x, "y_m": y} for x, y in ((-1, -1), (1, -1), (1, 0), (0, 0), (0, 1), (-1, 1))]
    for i in range(count):
        geometry = {"type": "polygon", "vertices": polygon} if i % 10 == 0 else (
            {"type": "circle", "radius_m": 0.8} if i % 10 == 1 else {"type": "box"})
        db.add(SpatialObject(
            scene_id=scene.id, name=f"Object {i}", object_type="fixture", category="fixture", parent_object_id=parent,
            order_index=i, width_m=2, depth_m=2, height_m=1,
            position_x_m=3 + i % 25 * spacing, position_y_m=3 + i // 25 * spacing, position_z_m=2,
            rotation_z_deg=i % 37, properties={"geometry": geometry},
        ))
    db.commit()
    return scene.id


def measure(request, *, memory=False):
    request().raise_for_status()  # Warm the connection and route.
    times = []
    for _ in range(3):
        start = perf_counter()
        response = request()
        times.append((perf_counter() - start) * 1000)
        response.raise_for_status()
    peak_mib = None
    if memory:
        tracemalloc.start()
        try:
            request().raise_for_status()
            peak_mib = round(tracemalloc.get_traced_memory()[1] / 1024**2, 2)
        finally:
            tracemalloc.stop()
    return {"median_ms": round(median(times), 1), "bytes": len(response.content), "peak_python_mib": peak_mib}, response


def main():
    with isolated_database() as engine:
        def bench_db():
            with Session(engine, autoflush=False) as session:
                yield session

        with Session(engine) as db:
            token, org_id, project_id = seed_project(db)
            scenarios = [(count, "sparse", seed_scene(db, project_id, count, 3)) for count in (25, 100, 250, 500)]
            scenarios += [(100, "dense", seed_scene(db, project_id, 100, 0.2))]
        app.dependency_overrides[get_db] = bench_db
        try:
            with TestClient(app, headers={"Authorization": "Bearer " + token}) as client:
                for count, layout, scene_id in scenarios:
                    path = f"/api/v1/spatial/scenes/{scene_id}"
                    sync, response = measure(lambda: client.get(path + "/synchronization"), memory=count == 500)
                    assert response.json()["validation"]["status"] == "synchronized"
                    validation, response = measure(lambda: client.post(path + "/validation-report"), memory=count == 500)
                    print({"objects": count, "layout": layout, "sync": sync, "validation": validation,
                           "findings": response.json()["summary"]["total"]}, flush=True)
                catalog, response = measure(lambda: client.get(
                    f"/api/v1/object-library/organizations/{org_id}/definitions", params={"category": "furniture", "limit": 50},
                ))
                assert len(response.json()["items"]) == 50
                print({"catalog_definitions": 600, "page": 50, "search": catalog}, flush=True)
        finally:
            app.dependency_overrides.pop(get_db, None)


if __name__ == "__main__":
    main()
