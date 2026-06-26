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

class AuthRequest(BaseModel):
    email: str
    password: str
    redirect_url: str | None = None

@router.post("/signup")
def signup(req: AuthRequest, supabase: Client = Depends(get_supabase_client)):
    try:
        options = {}
        if req.redirect_url:
            options["email_redirect_to"] = req.redirect_url
            
        res = supabase.auth.sign_up(
            {
                "email": req.email,
                "password": req.password,
                "options": options,
            }
        )
        return {"message": "Signup successful. Please check your email to confirm."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/login")
def login(req: AuthRequest, supabase: Client = Depends(get_supabase_client)):
    try:
        res = supabase.auth.sign_in_with_password({
            "email": req.email,
            "password": req.password
        })
        return {
            "session": {
                "access_token": res.session.access_token,
                "refresh_token": res.session.refresh_token,
                "expires_at": res.session.expires_at
            },
            "user": {
                "id": res.user.id,
                "email": res.user.email
            }
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid email or password")
