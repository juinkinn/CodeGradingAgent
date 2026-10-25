from fastapi import APIRouter
from fastapi import Depends

from app.api.auth import require_workspace_access, router as auth_router
from app.api.assignment import router as assignment_router
from app.api.grading import router as grading_router
from app.api.health import router as health_router
from app.api.submission import router as submission_router


api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(health_router)
api_router.include_router(
    assignment_router,
    dependencies=[Depends(require_workspace_access)],
)
api_router.include_router(
    submission_router,
    dependencies=[Depends(require_workspace_access)],
)
api_router.include_router(
    grading_router,
    dependencies=[Depends(require_workspace_access)],
)
