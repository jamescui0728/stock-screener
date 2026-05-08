"""
用户认证路由：
  POST /api/auth/register  手机号 + 密码注册
  POST /api/auth/login     手机号 + 密码登录，返回 JWT
  GET  /api/auth/me        获取当前用户信息
  POST /api/auth/change-password  修改密码
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import (
    create_access_token, get_current_admin, get_current_user, hash_password,
    is_valid_phone, normalize_phone, verify_password,
)
from database import get_db
from models.models import PaperAccount, User, Watchlist

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── 请求/响应模型 ───────────────────────────────────────
class RegisterRequest(BaseModel):
    phone:    str = Field(..., description="手机号，11 位中国大陆号码")
    password: str = Field(..., min_length=6, max_length=64)
    name:     Optional[str] = Field(None, max_length=30, description="昵称（可选）")


class LoginRequest(BaseModel):
    phone:    str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=64)


def _user_public(user: User) -> dict:
    return {
        "id":            user.id,
        "phone":         user.phone,
        "name":          user.name or "",
        "is_admin":      bool(user.is_admin),
        "is_active":     bool(user.is_active),
        "created_at":    str(user.created_at) if user.created_at else None,
        "last_login_at": str(user.last_login_at) if user.last_login_at else None,
    }


# ── 注册 ───────────────────────────────────────────────
@router.post("/register")
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    phone = normalize_phone(body.phone)
    if not is_valid_phone(phone):
        raise HTTPException(400, "手机号格式不正确（须为中国大陆 11 位手机号）")

    if db.query(User).filter_by(phone=phone).first():
        raise HTTPException(400, "该手机号已注册，请直接登录")

    # 首个用户自动成为管理员
    is_first_user = db.query(User).count() == 0

    user = User(
        phone=phone,
        password_hash=hash_password(body.password),
        name=(body.name or "").strip() or None,
        is_admin=is_first_user,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # 注册成功立即签发 token（省掉一次登录请求）
    token = create_access_token(user)
    return {
        "message": "注册成功",
        "token": token,
        "user":  _user_public(user),
    }


# ── 登录 ───────────────────────────────────────────────
@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    phone = normalize_phone(body.phone)
    user  = db.query(User).filter_by(phone=phone).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "手机号或密码错误")
    if not user.is_active:
        raise HTTPException(403, "账户已被禁用，请联系管理员")

    user.last_login_at = datetime.utcnow()
    db.commit()

    token = create_access_token(user)
    return {
        "message": "登录成功",
        "token": token,
        "user":  _user_public(user),
    }


# ── 当前用户 ───────────────────────────────────────────
@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _user_public(user)


# ── 修改密码 ───────────────────────────────────────────
@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db:   Session = Depends(get_db),
):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "原密码不正确")
    if body.old_password == body.new_password:
        raise HTTPException(400, "新密码不能与旧密码相同")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"message": "密码修改成功，下次登录请使用新密码"}


# ── 登出（前端删 token 即可；这里提供一个接口让前端调方便） ──
@router.post("/logout")
def logout():
    return {"message": "已退出登录"}


# ════════════════════════════════════════════════════════════
# 用户管理（管理员专用）
# ════════════════════════════════════════════════════════════
class UserUpdateRequest(BaseModel):
    name:      Optional[str]  = None
    is_active: Optional[bool] = None
    is_admin:  Optional[bool] = None


class UserCreateRequest(BaseModel):
    phone:    str = Field(..., description="手机号，11 位中国大陆号码")
    password: str = Field(..., min_length=6, max_length=64)
    name:     Optional[str] = Field(None, max_length=30)
    is_admin: bool = False
    is_active: bool = True


class AdminResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=6, max_length=64)


def _user_admin_view(user: User, db: Session) -> dict:
    """管理员看到的用户视图：比 _user_public 多一些统计字段"""
    watchlist_count = db.query(Watchlist).filter_by(user_id=user.id).count()
    has_paper = db.query(PaperAccount).filter_by(user_id=user.id).first() is not None
    return {
        **_user_public(user),
        "watchlist_count":   watchlist_count,
        "has_paper_account": has_paper,
    }


@router.get("/users")
def list_users(
    admin: User = Depends(get_current_admin),
    db:    Session = Depends(get_db),
):
    """列出所有用户（管理员）"""
    users = db.query(User).order_by(User.id.asc()).all()
    return [_user_admin_view(u, db) for u in users]


@router.post("/users")
def create_user(
    body:  UserCreateRequest,
    admin: User = Depends(get_current_admin),
    db:    Session = Depends(get_db),
):
    """管理员创建新用户（无需走注册流程，不下发 token）"""
    phone = normalize_phone(body.phone)
    if not is_valid_phone(phone):
        raise HTTPException(400, "手机号格式不正确（须为中国大陆 11 位手机号）")
    if db.query(User).filter_by(phone=phone).first():
        raise HTTPException(400, "该手机号已注册")

    user = User(
        phone=phone,
        password_hash=hash_password(body.password),
        name=(body.name or "").strip() or None,
        is_admin=bool(body.is_admin),
        is_active=bool(body.is_active),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_admin_view(user, db)


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    body:  UserUpdateRequest,
    admin: User = Depends(get_current_admin),
    db:    Session = Depends(get_db),
):
    """修改用户的昵称/启禁用/管理员身份"""
    target = db.query(User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(404, "用户不存在")

    # 防呆：不能把自己禁用或撤销自己的管理员身份（避免锁死系统）
    if target.id == admin.id:
        if body.is_active is False:
            raise HTTPException(400, "不能禁用自己")
        if body.is_admin is False:
            raise HTTPException(400, "不能撤销自己的管理员身份")

    if body.name is not None:
        target.name = (body.name or "").strip() or None
    if body.is_active is not None:
        target.is_active = bool(body.is_active)
    if body.is_admin is not None:
        target.is_admin = bool(body.is_admin)

    db.commit()
    db.refresh(target)
    return _user_admin_view(target, db)


@router.post("/users/{user_id}/reset-password")
def admin_reset_password(
    user_id: int,
    body:  AdminResetPasswordRequest,
    admin: User = Depends(get_current_admin),
    db:    Session = Depends(get_db),
):
    """管理员重置他人密码（无需知道旧密码）"""
    target = db.query(User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(404, "用户不存在")
    target.password_hash = hash_password(body.new_password)
    db.commit()
    return {"message": f"已重置 {target.phone} 的密码"}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    admin: User = Depends(get_current_admin),
    db:    Session = Depends(get_db),
):
    """删除用户（级联删除自选股和模拟盘账户）"""
    target = db.query(User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(404, "用户不存在")
    if target.id == admin.id:
        raise HTTPException(400, "不能删除自己")

    phone = target.phone
    db.delete(target)   # cascade 会带走 watchlist + paper_account
    db.commit()
    return {"message": f"已删除用户 {phone}"}
