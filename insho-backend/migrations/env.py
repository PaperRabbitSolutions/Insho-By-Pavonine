
from logging.config import fileConfig

from alembic import context

from app.db.database import engine, DATABASE_URL
from app.db.base import Base

# Import models so their tables are registered in Base.metadata.
# Import models so their tables are registered in Base.metadata.
from app.models.user import User
from app.models.organization import Organization
from app.models.organization_member import OrganizationMember
from app.models.workspace import Workspace
from app.models.project import Project
from app.models.design_version import DesignActivity, DesignBranch, DesignSnapshot, DesignSnapshotAsset, DesignVersion, DesignVersionReview
from app.models.design_audit import DesignAudit
from app.models.learning import LearningActual, LearningDisposition, LearningReport
from app.models.presentation import Presentation, PresentationAsset, PresentationCheckpoint, PresentationRevision
from app.models.production import (
    ProductionItem, ProductionMaterial, ProductionMeasurement, ProductionQuantity, ProductionRecord,
    ProductionSpecification, ProductionWorkflowRevision,
)
from app.models.production_bom import MaterialDefinition, MaterialDefinitionRevision, ProductionBOM, ProductionBOMLine
from app.models.production_procurement import (
    ProductionBudget, ProductionProcurement, ProductionProjectSupplier, ProductionQuotation, ProductionSupplier,
    ProductionSupplierOffering, ProductionValueProposal,
)
from app.models.event import Event
from app.models.event_type import EventType
from app.models.venue import Venue
from app.models.venue_zone import VenueZone
from app.models.venue_source import VenueSource
from app.models.design_dna import DesignDNA
from app.models.project_memory import ProjectMemory
from app.models.project_memory_item import ProjectMemoryItem, ProjectMemoryRevision
from app.models.user_session import UserSession
from app.models.organization_invitation import OrganizationInvitation
from app.models.object_library import ObjectDefinition, ObjectDefinitionAsset
from app.models.asset import Asset
from app.models.asset_folder import AssetFolder
from app.models.asset_derivative import AssetDerivative
from app.models.ai_execution import AIExecution
from app.models.design_artifact import DesignArtifact, DesignElement, DesignArtifactAsset
from app.models.spatial_design_review import SpatialDesignReview
from app.models.spatial import (
    SpatialScene, SpatialZone, SpatialObject, SpatialObjectRelationship,
    SpatialObjectAsset, SpatialSceneRevision,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
