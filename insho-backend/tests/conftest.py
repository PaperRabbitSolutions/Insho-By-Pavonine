
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db.base import Base
from app.db.database import get_db
from app.auth import create_session, hash_password

# Import all models so their tables register with Base.metadata.
from app.models.user import User
from app.models.organization import Organization
from app.models.organization_member import OrganizationMember
from app.models.event_type import EventType
from app.models.event import Event
from app.models.venue import Venue
from app.models.venue_zone import VenueZone
from app.models.venue_source import VenueSource
from app.models.design_dna import DesignDNA
from app.models.project_memory import ProjectMemory
from app.models.project_memory_item import ProjectMemoryItem
from app.models.project import Project
from app.models.workspace import Workspace
from app.models.user_session import UserSession
from app.models.organization_invitation import OrganizationInvitation
from app.models.asset import Asset
from app.models.asset_folder import AssetFolder
from app.models.asset_derivative import AssetDerivative
from app.models.spatial import (
    SpatialScene, SpatialZone, SpatialObject, SpatialObjectRelationship,
    SpatialObjectAsset, SpatialSceneRevision,
)


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

if not TEST_DATABASE_URL:
    raise RuntimeError("TEST_DATABASE_URL is missing")


if "insho_test_db" not in TEST_DATABASE_URL:
    raise RuntimeError(
        "Tests must use the insho_test_db database"
    )


test_engine = create_engine(TEST_DATABASE_URL)


@pytest.fixture(autouse=True)
def disable_external_asset_queue(monkeypatch):
    # Upload tests verify persistence and processing services directly; no Redis is needed.
    from app.workers.tasks import process_asset

    monkeypatch.setattr(process_asset, "apply_async", lambda *_args, **_kwargs: None)

TestingSessionLocal = sessionmaker(
    bind=test_engine,
    autoflush=False,
    autocommit=False,
)


@pytest.fixture
def identity_factory(db_session):
    def create_identity(role="owner", email=None, password=None, organization=None):
        suffix = uuid.uuid4().hex
        user = User(
            full_name="Test User",
            email=email or f"user-{suffix}@example.test",
            password_hash=hash_password(password) if password else "test-session-only",
        )
        if organization is None:
            organization = Organization(name="Test Organization", slug=f"test-{suffix}")
            db_session.add(organization)
        db_session.add(user)
        db_session.flush()
        workspace = Workspace(
            organization_id=organization.id,
            name="Test Workspace",
            slug=f"workspace-{suffix}",
        )
        membership = OrganizationMember(
            user_id=user.id,
            organization_id=organization.id,
            role=role,
        )
        db_session.add_all([workspace, membership])
        token, _ = create_session(db_session, user)
        db_session.commit()
        return {
            "user": user,
            "organization": organization,
            "workspace": workspace,
            "token": token,
        }

    return create_identity


@pytest.fixture(scope="session", autouse=True)
def create_test_tables():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session():
    db = TestingSessionLocal()

    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    # The existing domain CRUD tests are exercised as an authorized owner.
    # Security tests use unauthenticated_client and explicit alternate tokens.
    # Seed records through the fixture DB, never the development database.
    suffix = uuid.uuid4().hex
    user = User(
        full_name="Test Owner",
        email=f"owner-{suffix}@example.test",
        password_hash="test-session-only",
        is_system_admin=True,
    )
    organization = Organization(name="Test Organization", slug=f"owner-{suffix}")
    db_session.add_all([user, organization])
    db_session.flush()
    db_session.add(
        OrganizationMember(
            user_id=user.id,
            organization_id=organization.id,
            role="owner",
        )
    )
    token, _ = create_session(db_session, user)
    db_session.commit()

    with TestClient(app) as test_client:
        test_client.headers["Authorization"] = "Bearer " + token
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def unauthenticated_client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
