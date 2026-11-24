from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repositories.design_dna import DesignDNARepository
from app.schemas.design_dna import DesignDNACreate, DesignDNAUpdate
from app.security import require_project_access


class DesignDNAService:

    @staticmethod
    def create(db: Session, data: DesignDNACreate, user_id: UUID):
        require_project_access(db, user_id, data.project_id, write=True)

        existing = DesignDNARepository.get_by_project(
            db, data.project_id
        )

        if existing:
            raise HTTPException(
                status_code=409,
                detail="Design DNA already exists for this project"
            )

        return DesignDNARepository.create(db, data.model_dump())

    @staticmethod
    def get_by_id(db: Session, design_dna_id: UUID, user_id: UUID):
        design_dna = DesignDNARepository.get_by_id(
            db, design_dna_id
        )

        if not design_dna:
            raise HTTPException(
                status_code=404,
                detail="Design DNA not found"
            )
        require_project_access(db, user_id, design_dna.project_id)

        return design_dna

    @staticmethod
    def get_by_project(db: Session, project_id: UUID, user_id: UUID):
        require_project_access(db, user_id, project_id)

        design_dna = DesignDNARepository.get_by_project(
            db, project_id
        )

        if not design_dna:
            raise HTTPException(
                status_code=404,
                detail="Design DNA not found for this project"
            )

        return design_dna

    @staticmethod
    def update(db: Session, design_dna_id: UUID, data: DesignDNAUpdate, user_id: UUID):
        design_dna = DesignDNAService.get_by_id(
            db, design_dna_id, user_id
        )
        require_project_access(db, user_id, design_dna.project_id, write=True)

        payload = data.model_dump(exclude_unset=True)

        return DesignDNARepository.update(
            db, design_dna, payload
        )

    @staticmethod
    def delete(db: Session, design_dna_id: UUID, user_id: UUID):
        design_dna = DesignDNAService.get_by_id(
            db, design_dna_id, user_id
        )
        require_project_access(db, user_id, design_dna.project_id, write=True)

        DesignDNARepository.delete(db, design_dna)
