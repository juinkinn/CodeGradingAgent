from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.crud import assignment as assignment_crud
from app.crud import submission as submission_crud
from app.crud import submission_upload as submission_upload_crud
from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.infrastructure.rabbitmq import (
    RabbitMQUnavailable,
    publish_submission_upload,
)
from app.schemas.submission import (
    SubmissionCreate,
    SubmissionResponse,
    SubmissionUpdate,
    SubmissionUploadBatchResponse,
)
from app.services.submission_upload import (
    MAX_SOURCE_FILES,
    MAX_UPLOAD_BYTES,
    UploadValidationError,
    collect_source_files,
)


router = APIRouter(tags=["submissions"])


def _read_uploads(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    if not files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one source file or ZIP archive is required.",
        )
    if len(files) > MAX_SOURCE_FILES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"An upload request may contain at most {MAX_SOURCE_FILES} files.",
        )

    uploads = []
    total_bytes = 0
    for upload in files:
        if not upload.filename:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Every uploaded file must have a filename.",
            )
        content = upload.file.read(MAX_UPLOAD_BYTES - total_bytes + 1)
        total_bytes += len(content)
        if total_bytes > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Compressed upload files exceed the 25 MiB request limit.",
            )
        uploads.append((upload.filename, content))
    return uploads


@router.post(
    "/submissions",
    response_model=SubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_submission(
    data: SubmissionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assignment = assignment_crud.get(db, data.assignment_id)
    if assignment is None or assignment.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignment not found",
        )
    return submission_crud.create(db, data)


@router.post(
    "/assignments/{assignment_id}/submissions/upload",
    response_model=SubmissionUploadBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_assignment_submissions(
    assignment_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    if assignment_crud.get(db, assignment_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignment not found",
        )

    try:
        source_files = collect_source_files(_read_uploads(files))
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    batch = submission_upload_crud.create_batch(
        db,
        assignment_id,
        [(source.filename, source.source_code) for source in source_files],
    )
    try:
        publish_submission_upload(batch.id)
    except RabbitMQUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": str(exc),
                "batch_id": batch.id,
                "retry_endpoint": f"/submission-upload-batches/{batch.id}/retry",
            },
        ) from exc
    return batch


@router.get(
    "/submission-upload-batches/{batch_id}",
    response_model=SubmissionUploadBatchResponse,
)
def get_submission_upload_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = submission_upload_crud.get_batch(db, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submission upload batch not found",
        )
    return batch


@router.post(
    "/submission-upload-batches/{batch_id}/retry",
    response_model=SubmissionUploadBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_submission_upload_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = submission_upload_crud.get_batch(db, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submission upload batch not found",
        )
    if batch.status == "PROCESSING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The upload batch is already being processed.",
        )

    retry_items = [item for item in batch.items if item.status in {"QUEUED", "FAILED"}]
    if not retry_items:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The upload batch has no pending or failed files to retry.",
        )
    for item in retry_items:
        item.status = "QUEUED"
        item.error = None
    batch.status = "QUEUED"
    db.commit()

    try:
        publish_submission_upload(batch.id)
    except RabbitMQUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": str(exc),
                "batch_id": batch.id,
                "retry_endpoint": f"/submission-upload-batches/{batch.id}/retry",
            },
        ) from exc
    return submission_upload_crud.get_batch(db, batch_id)


@router.get(
    "/assignments/{assignment_id}/submissions",
    response_model=list[SubmissionResponse],
)
def get_submissions_by_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
):
    if assignment_crud.get(db, assignment_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignment not found",
        )
    return submission_crud.get_by_assignment(db, assignment_id)


@router.get("/submissions/{submission_id}", response_model=SubmissionResponse)
def get_submission(submission_id: int, db: Session = Depends(get_db)):
    submission = submission_crud.get(db, submission_id)
    if submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submission not found",
        )
    return submission


@router.patch(
    "/submissions/{submission_id}",
    response_model=SubmissionResponse,
)
def update_submission(
    submission_id: int,
    data: SubmissionUpdate,
    db: Session = Depends(get_db),
):
    submission = submission_crud.get(db, submission_id)
    if submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submission not found",
        )
    return submission_crud.update(db, submission, data)


@router.delete(
    "/submissions/{submission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_submission(submission_id: int, db: Session = Depends(get_db)):
    submission = submission_crud.get(db, submission_id)
    if submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submission not found",
        )
    submission_crud.delete(db, submission)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
