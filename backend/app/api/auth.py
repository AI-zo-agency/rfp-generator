import asyncio

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from supabase import create_client, Client
from app.core.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


def get_supabase_client() -> Client:
    try:
        url = settings.supabase_url.strip()
        key = getattr(settings, "supabase_anon_key", settings.supabase_service_role_key).strip()
        return create_client(url, key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Supabase client error: {str(e)}")


ALLOWED_DOMAIN = "zo.agency"


class AuthRequest(BaseModel):
    email: str
    password: str
    redirect_url: str | None = None


def _validate_domain(email: str) -> None:
    domain = email.strip().rsplit("@", 1)[-1].lower()
    if domain != ALLOWED_DOMAIN:
        raise HTTPException(
            status_code=403,
            detail="Different domain not allowed.",
        )


@router.options("/login")
@router.options("/signup")
async def auth_options():
    return {}


@router.post("/signup")
async def signup(req: AuthRequest, supabase: Client = Depends(get_supabase_client)):
    _validate_domain(req.email)
    try:
        options = {}
        if req.redirect_url:
            options["email_redirect_to"] = req.redirect_url

        await asyncio.to_thread(
            supabase.auth.sign_up,
            {
                "email": req.email,
                "password": req.password,
                "options": options,
            },
        )
        return {"message": "Signup successful. Please check your email to confirm."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.options("/refresh")
async def refresh_options():
    return {}


class RefreshRequest(BaseModel):
    refresh_token: str


def _session_payload(res) -> dict:
    return {
        "session": {
            "access_token": res.session.access_token,
            "refresh_token": res.session.refresh_token,
            "expires_at": res.session.expires_at,
        },
        "user": {
            "id": res.user.id,
            "email": res.user.email,
        },
    }


@router.post("/login")
async def login(req: AuthRequest, supabase: Client = Depends(get_supabase_client)):
    try:
        res = await asyncio.to_thread(
            supabase.auth.sign_in_with_password,
            {
                "email": req.email,
                "password": req.password,
            },
        )
        return _session_payload(res)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid email or password")


@router.post("/refresh")
async def refresh(req: RefreshRequest, supabase: Client = Depends(get_supabase_client)):
    """Trade a refresh token for a new access token; access tokens last about an hour."""
    try:
        res = await asyncio.to_thread(supabase.auth.refresh_session, req.refresh_token)
        return _session_payload(res)
    except Exception:
        raise HTTPException(status_code=401, detail="Session expired, sign in again")

