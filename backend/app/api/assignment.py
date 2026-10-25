from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.crud import assignment as assignment_crud
from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.schemas.assignment import (
    AssignmentCreate,
    AssignmentResponse,
    AssignmentUpdate,
)


router = APIRouter(prefix="/assignments", tags=["assignments"])


@router.post(
    "",
    response_model=AssignmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_assignment(
    data: AssignmentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return assignment_crud.create(db, data, owner_id=user.id)


@router.get("", response_model=list[AssignmentResponse])
def get_assignments(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return assignment_crud.get_all(db, owner_id=user.id, skip=skip, limit=limit)


@router.get("/{assignment_id}", response_model=AssignmentResponse)
def get_assignment(assignment_id: int, db: Session = Depends(get_db)):
    assignment = assignment_crud.get(db, assignment_id)
    if assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignment not found",
        )
    return assignment


@router.patch("/{assignment_id}", response_model=AssignmentResponse)
def update_assignment(
    assignment_id: int,
    data: AssignmentUpdate,
    db: Session = Depends(get_db),
):
    assignment = assignment_crud.get(db, assignment_id)
    if assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignment not found",
        )
    return assignment_crud.update(db, assignment, data)


@router.delete("/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_assignment(assignment_id: int, db: Session = Depends(get_db)):
    assignment = assignment_crud.get(db, assignment_id)
    if assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignment not found",
        )
    assignment_crud.delete(db, assignment)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
