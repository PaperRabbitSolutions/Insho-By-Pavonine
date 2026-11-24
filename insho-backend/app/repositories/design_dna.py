from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.design_dna import DesignDNA


class DesignDNARepository:

    @staticmethod
    def create(db: Session, data: dict) -> DesignDNA:
        design_dna = DesignDNA(**data)
        db.add(design_dna)
        db.commit()
        db.refresh(design_dna)
        return design_dna

    @staticmethod
    def get_by_id(db: Session, design_dna_id: UUID):
        statement = select(DesignDNA).where(
            DesignDNA.id == design_dna_id
        )
        return db.scalars(statement).first()

    @staticmethod
    def get_by_project(db: Session, project_id: UUID):
        statement = select(DesignDNA).where(
            DesignDNA.project_id == project_id
        )
        return db.scalars(statement).first()

    @staticmethod
    def update(db: Session, design_dna: DesignDNA, data: dict):
        for field, value in data.items():
            setattr(design_dna, field, value)

        design_dna.version += 1

        db.commit()
        db.refresh(design_dna)
        return design_dna

    @staticmethod
    def delete(db: Session, design_dna: DesignDNA):
        db.delete(design_dna)
        db.commit()
