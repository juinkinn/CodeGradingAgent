import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, Request as FastAPIRequest, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.env import settings
from app.models.assignment import Assignment
from app.models.grading_session import GradingSession
from app.models.submission import Submission
from app.models.submission_upload import SubmissionUploadBatch
from app.models.user import User
from app.schemas.auth import Credentials, UserResponse
from app.services.auth import (
    create_oauth_state,
    create_session_token,
    create_user,
    google_authorization_url,
    normalize_email,
    read_session_token,
    session_serializer,
    verify_oauth_state,
    verify_password,
)


router = APIRouter(prefix="/auth", tags=["authentication"])
logger = logging.getLogger(__name__)
SESSION_COOKIE = "codegrading_session"
OAUTH_STATE_COOKIE = "codegrading_oauth_state"


def get_current_user(
    request: FastAPIRequest,
    db: Session = Depends(get_db),
) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        user_id = read_session_token(token)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    user = db.query(User).filter(User.id == user_id).first() if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="Session expired. Please sign in.")
    return user


def require_workspace_access(
    request: FastAPIRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        _validate_origin(request)
    params = request.path_params
    assignment_id = params.get("assignment_id")
    if assignment_id is not None:
        assignment = (
            db.query(Assignment)
            .filter(
                Assignment.id == int(assignment_id),
                Assignment.owner_id == user.id,
            )
            .first()
        )
        if assignment is None:
            raise HTTPException(status_code=404, detail="Assignment not found.")
        return

    session_id = params.get("session_id")
    if session_id is not None:
        exists = (
            db.query(GradingSession.id)
            .join(Assignment)
            .filter(
                GradingSession.id == int(session_id),
                Assignment.owner_id == user.id,
            )
            .first()
        )
        if exists is None:
            raise HTTPException(status_code=404, detail="Grading session not found.")
        return

    submission_id = params.get("submission_id")
    if submission_id is not None:
        exists = (
            db.query(Submission.id)
            .join(Assignment)
            .filter(
                Submission.id == int(submission_id),
                Assignment.owner_id == user.id,
            )
            .first()
        )
        if exists is None:
            raise HTTPException(status_code=404, detail="Submission not found.")
        return

    batch_id = params.get("batch_id")
    if batch_id is not None:
        exists = (
            db.query(SubmissionUploadBatch.id)
            .join(Assignment)
            .filter(
                SubmissionUploadBatch.id == int(batch_id),
                Assignment.owner_id == user.id,
            )
            .first()
        )
        if exists is None:
            raise HTTPException(
                status_code=404,
                detail="Submission upload batch not found.",
            )


def _validate_origin(request: FastAPIRequest) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in {
        allowed.rstrip("/") for allowed in settings.cors_origins
    }:
        raise HTTPException(status_code=403, detail="Request origin is not allowed.")


def _set_session_cookie(response: Response, user: User) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user.id),
        max_age=settings.auth_session_max_age,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def _create_account(
    db: Session,
    *,
    email: str,
    password: str | None = None,
    google_subject: str | None = None,
) -> User:
    try:
        return create_user(
            db,
            email,
            password=password,
            google_subject=google_subject,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/register", response_model=UserResponse, status_code=201)
def register(
    credentials: Credentials,
    request: FastAPIRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    _validate_origin(request)
    try:
        email = normalize_email(credentials.email)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if db.query(User.id).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    try:
        session_serializer()
        user = _create_account(db, email=email, password=credentials.password)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        ) from exc
    _set_session_cookie(response, user)
    return user


@router.post("/login", response_model=UserResponse)
def login(
    credentials: Credentials,
    request: FastAPIRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    _validate_origin(request)
    try:
        email = normalize_email(credentials.email)
    except ValueError:
        email = credentials.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    try:
        _set_session_cookie(response, user)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return user


@router.get("/me", response_model=UserResponse)
def current_user(user: User = Depends(get_current_user)):
    return user


@router.post("/logout", status_code=204)
def logout(request: FastAPIRequest, response: Response):
    _validate_origin(request)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.status_code = 204
    return response


@router.get("/google")
def google_login(request: FastAPIRequest):
    if not settings.google_client_id or not settings.google_client_secret:
        return RedirectResponse(
            f"{settings.frontend_url}/?auth_error=google_unconfigured"
        )
    try:
        session_serializer()
        state, state_token = create_oauth_state()
        redirect_uri = str(request.url_for("google_callback"))
        response = RedirectResponse(google_authorization_url(redirect_uri, state))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state_token,
        max_age=600,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/auth/google",
    )
    return response


def _google_user_info(code: str, redirect_uri: str) -> dict:
    if not settings.google_client_id or not settings.google_client_secret:
        raise RuntimeError("Google OAuth client credentials are not configured.")
    token_request = Request(
        "https://oauth2.googleapis.com/token",
        data=urlencode(
            {
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }
        ).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(token_request, timeout=15) as token_response:
        token_payload = json.loads(token_response.read())
    access_token = token_payload.get("access_token")
    if not isinstance(access_token, str):
        raise ValueError("Google did not return an access token.")
    userinfo_request = Request(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urlopen(userinfo_request, timeout=15) as userinfo_response:
        user_info = json.loads(userinfo_response.read())
    if user_info.get("email_verified") is not True:
        raise ValueError("Google account email has not been verified.")
    if not isinstance(user_info.get("sub"), str) or not isinstance(
        user_info.get("email"), str
    ):
        raise ValueError("Google returned incomplete account details.")
    return user_info


@router.get("/google/callback", name="google_callback")
def google_callback(
    request: FastAPIRequest,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    state_token = request.cookies.get(OAUTH_STATE_COOKIE)
    clear_cookie = {
        "key": OAUTH_STATE_COOKIE,
        "path": "/auth/google",
        "secure": settings.auth_cookie_secure,
        "httponly": True,
        "samesite": "lax",
    }
    if error or not code or not state or not state_token:
        response = RedirectResponse(f"{settings.frontend_url}/?auth_error=google_cancelled")
        response.delete_cookie(**clear_cookie)
        return response
    try:
        if not verify_oauth_state(state, state_token):
            raise ValueError("Google sign-in state did not match.")
        user_info = _google_user_info(code, str(request.url_for("google_callback")))
        email = normalize_email(user_info["email"])
        user = (
            db.query(User)
            .filter(User.google_subject == user_info["sub"])
            .first()
        )
        if user is None:
            user = db.query(User).filter(User.email == email).first()
            if user:
                user.google_subject = user_info["sub"]
                db.commit()
                db.refresh(user)
            else:
                user = _create_account(
                    db,
                    email=email,
                    google_subject=user_info["sub"],
                )
        response = RedirectResponse(settings.frontend_url)
        response.set_cookie(
            SESSION_COOKIE,
            create_session_token(user.id),
            max_age=settings.auth_session_max_age,
            httponly=True,
            secure=settings.auth_cookie_secure,
            samesite="lax",
            path="/",
        )
        response.delete_cookie(**clear_cookie)
        return response
    except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError):
        logger.exception("Google sign-in failed")
        response = RedirectResponse(f"{settings.frontend_url}/?auth_error=google_failed")
        response.delete_cookie(**clear_cookie)
        return response
