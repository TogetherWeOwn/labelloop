"""Deny-by-default API boundary and same-origin server-side session routes."""

import hmac
import os
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from labelloop_api.auth_store import SESSION_LIFETIME, AuthStore, Principal, digest
from labelloop_api.database_url import quoted_database_url

COOKIE = "__Host-labelloop-session"
CSRF_COOKIE = "__Host-labelloop-csrf"
router = APIRouter(prefix="/auth", tags=["auth"])


@lru_cache(maxsize=1)
def auth_store() -> AuthStore:
    return AuthStore(create_engine(quoted_database_url(), pool_pre_ping=True))


def clock() -> datetime:
    return datetime.now(UTC)


def same_origin(request: Request) -> bool:
    origin = os.environ.get("AUTH_ORIGIN")
    return bool(origin and request.headers.get("origin") == origin)


def authenticate_request(request: Request) -> Response | None:
    authorization = request.headers.get("authorization")
    cookie = request.cookies.get(COOKIE)
    if authorization and cookie:
        return JSONResponse({"detail": "ambiguous_credentials"}, status_code=401)
    service = authorization is not None
    if service:
        scheme, separator, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator:
            return JSONResponse({"detail": "authentication_required"}, status_code=401)
    else:
        token = cookie
    if not token:
        return JSONResponse({"detail": "authentication_required"}, status_code=401)
    store = auth_store()
    now = clock()
    authentication = store.authenticate(token, service=service, now=now)
    if authentication is None:
        return JSONResponse({"detail": "authentication_required"}, status_code=401)
    actor = authentication.principal
    if not service and request.method not in ("GET", "HEAD", "OPTIONS"):
        csrf = request.headers.get("x-csrf-token", "")
        if not same_origin(request) or not hmac.compare_digest(
            digest(csrf), authentication.csrf_hash or ""
        ):
            store.event(actor, "csrf_denied", now)
            return JSONResponse({"detail": "csrf_denied"}, status_code=403)
    request.state.principal = actor
    return None


async def authentication_boundary(request: Request, call_next):
    # Only these exact method/path pairs are anonymous. New routes inherit the guard.
    public = (request.method, request.url.path) in (
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("POST", "/auth/login"),
    )
    response = None
    if not public:
        try:
            response = await run_in_threadpool(authenticate_request, request)
        except SQLAlchemyError:
            response = JSONResponse({"detail": "authentication_unavailable"}, status_code=503)
    if response is None:
        response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


def current_principal(request: Request) -> Principal:
    actor = getattr(request.state, "principal", None)
    if not isinstance(actor, Principal):
        raise HTTPException(401, detail="authentication_required")
    return actor


def require_permission(request: Request, permission: str) -> Principal:
    actor = current_principal(request)
    if not actor.permits(permission):
        try:
            auth_store().event(actor, "permission_denied", clock())
        except SQLAlchemyError:
            raise HTTPException(503, detail="authentication_unavailable") from None
        raise HTTPException(403, detail="permission_denied")
    return actor


def require_admin(request: Request) -> Principal:
    return require_permission(request, "admin")


def require_reviewer(request: Request) -> Principal:
    return require_permission(request, "review")


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024)


@router.post("/login")
def login(payload: Login, request: Request, response: Response) -> dict[str, str]:
    if not same_origin(request):
        raise HTTPException(403, detail="csrf_denied")
    try:
        result = auth_store().login(payload.name, payload.password.get_secret_value(), clock())
    except SQLAlchemyError:
        raise HTTPException(503, detail="authentication_unavailable") from None
    if result is None:
        raise HTTPException(401, detail="invalid_credentials")
    token, csrf = result
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )
    # The browser can recover this proof after reload without exposing the session token.
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        secure=True,
        httponly=False,
        samesite="strict",
        path="/",
    )
    return {"csrf_token": csrf}


@router.get("/me")
def me(actor: Annotated[Principal, Depends(current_principal)]) -> dict[str, str]:
    return {"id": actor.id, "name": actor.name, "kind": actor.kind, "role": actor.role}


@router.post("/logout", status_code=204)
def logout(request: Request, actor: Annotated[Principal, Depends(require_reviewer)]) -> Response:
    try:
        auth_store().logout(request.cookies[COOKIE], actor, clock())
    except SQLAlchemyError:
        raise HTTPException(503, detail="authentication_unavailable") from None
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict", path="/")
    response.delete_cookie(CSRF_COOKIE, secure=True, samesite="strict", path="/")
    return response


async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    # FastAPI's default validation response includes raw invalid inputs (even passwords).
    return JSONResponse({"detail": "invalid_request"}, status_code=422)
