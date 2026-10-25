from sqlalchemy.orm import Session

from app.models.assignment import Assignment
from app.schemas.assignment import AssignmentCreate, AssignmentUpdate


def create(db: Session, data: AssignmentCreate, *, owner_id: int):
    assignment = Assignment(**data.model_dump(), owner_id=owner_id)
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment


def get(db: Session, assignment_id: int):
    return db.query(Assignment).filter(Assignment.id == assignment_id).first()


def get_all(db: Session, *, owner_id: int, skip: int = 0, limit: int = 100):
    return (
        db.query(Assignment)
        .filter(Assignment.owner_id == owner_id)
        .offset(skip)
        .limit(limit)
        .all()
    )


def update(db: Session, assignment: Assignment, data: AssignmentUpdate):
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(assignment, key, value)

    db.commit()
    db.refresh(assignment)
    return assignment


def delete(db: Session, assignment: Assignment):
    db.delete(assignment)
    db.commit()