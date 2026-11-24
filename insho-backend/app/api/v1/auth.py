from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context, get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.auth import (
    AcceptInvitationRequest,
    InvitationAcceptedResponse,
    LoginRequest,
    TokenResponse,
    UserResponse,
)
from app.services import auth as AuthService
from app.services.organization_access import accept_invitation

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user, token, expires_at = AuthService.login(db, data)
    return {"access_token": token, "expires_at": expires_at, "user": user}


@router.get("/me", response_model=UserResponse)
def current_user(user: User = Depends(get_current_user)):
    return user


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    user, token, expires_at = AuthService.refresh(db, context)
    return {"access_token": token, "expires_at": expires_at, "user": user}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    AuthService.logout(db, context)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/accept-invitation", response_model=InvitationAcceptedResponse)
def accept_organization_invitation(
    data: AcceptInvitationRequest,
    db: Session = Depends(get_db),
):
    return accept_invitation(db, data)
