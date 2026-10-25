from fastapi import APIRouter


router = APIRouter(tags=["health"])


@router.get("/")
def health_check():
    return {"status": "ok"}
