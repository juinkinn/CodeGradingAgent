import base64
import hashlib
import hmac
import re
import secrets
from urllib.parse import urlencode

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.env import settings
from app.models.assignment import Assignment
from app.models.user import User


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_ITERATIONS = 600_000


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if not EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("Enter a valid email address.")
    return normalized


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded_hash: str | None) -> bool:
    if not encoded_hash:
        return False
    try:
        algorithm, rounds, salt_text, digest_text = encoded_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(digest_text)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(rounds),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def session_serializer() -> URLSafeTimedSerializer:
    if not settings.auth_secret_key:
        raise RuntimeError("AUTH_SECRET_KEY must be configured.")
    return URLSafeTimedSerializer(settings.auth_secret_key, salt="auth-session-v1")


def create_session_token(user_id: int) -> str:
    return session_serializer().dumps({"user_id": user_id})


def read_session_token(token: str) -> int | None:
    try:
        payload = session_serializer().loads(token, max_age=settings.auth_session_max_age)
    except (BadSignature, SignatureExpired):
        return None
    user_id = payload.get("user_id") if isinstance(payload, dict) else None
    return user_id if isinstance(user_id, int) and user_id > 0 else None


def create_oauth_state() -> tuple[str, str]:
    state = secrets.token_urlsafe(32)
    token = session_serializer().dumps({"state": state})
    return state, token


def verify_oauth_state(state: str, token: str) -> bool:
    try:
        payload = session_serializer().loads(token, max_age=600)
    except (BadSignature, SignatureExpired):
        return False
    return isinstance(payload, dict) and hmac.compare_digest(
        str(payload.get("state", "")),
        state,
    )


def create_user(db: Session, email: str, *, password: str | None = None,
                google_subject: str | None = None) -> User:
    user = User(
        email=normalize_email(email),
        password_hash=hash_password(password) if password is not None else None,
        google_subject=google_subject,
    )
    db.add(user)
    try:
        db.flush()
        user_count = db.query(func.count(User.id)).scalar()
        if user_count == 1:
            db.query(Assignment).filter(Assignment.owner_id.is_(None)).update(
                {Assignment.owner_id: user.id},
                synchronize_session=False,
            )
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise
    return user


def google_authorization_url(redirect_uri: str, state: str) -> str:
    if not settings.google_client_id:
        raise RuntimeError("GOOGLE_CLIENT_ID is not configured.")
    query = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"
