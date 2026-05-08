"""
用户认证工具：密码哈希 / JWT 签发验证 / FastAPI 依赖。

设计：
- 密码：bcrypt + passlib，自动盐 + cost factor
- Token：JWT（HS256），载荷 {sub: user_id, phone, exp}
- 依赖：get_current_user() 从 Authorization: Bearer <token> 中解析并查库
  get_current_user_optional() 用于兼容"旧数据无用户"的过渡期
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models.models import User


# ── 密码哈希（bcrypt 直连，绕过 passlib 与 bcrypt 4.x 的兼容问题） ──
# bcrypt 上限 72 字节，超过部分会被忽略；对手机号场景下的人类密码足够。
def hash_password(raw: str) -> str:
    pw = (raw or "").encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw((raw or "").encode("utf-8")[:72],
                              (hashed or "").encode("utf-8"))
    except Exception:
        return False


# ── JWT ──────────────────────────────────────────────────
def create_access_token(user: User) -> str:
    payload = {
        "sub":   str(user.id),
        "phone": user.phone,
        "name":  user.name or "",
        "exp":   datetime.utcnow() + timedelta(hours=int(settings.JWT_EXPIRE_HOURS)),
        "iat":   datetime.utcnow(),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(
        token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM],
    )


# ── 手机号规范化 / 校验 ────────────────────────────────────
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")   # 中国大陆 11 位手机号


def normalize_phone(raw: str) -> str:
    """去空格/连字符，返回纯数字。不做格式校验（校验交给 validate_phone）"""
    return re.sub(r"[\s\-]", "", raw or "")


def is_valid_phone(phone: str) -> bool:
    return bool(_PHONE_RE.match(phone or ""))


# ── FastAPI 依赖 ─────────────────────────────────────────
def _extract_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """解析 Bearer Token → User。无/失败一律 401"""
    token = _extract_token(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录，请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub", 0))
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "登录已过期，请重新登录")
    except Exception:
        raise HTTPException(401, "登录凭证无效")

    user = db.query(User).filter_by(id=user_id).first()
    if not user or not user.is_active:
        raise HTTPException(401, "账户不存在或已禁用")
    return user


def get_current_admin(
    user: User = Depends(get_current_user),
) -> User:
    """管理员专用依赖：非管理员返回 403"""
    if not user.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return user


def get_current_user_optional(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """
    宽松版：拿到就返回，拿不到返回 None。
    给过渡期的公共接口（行业/公司/回测）用，保持向后兼容。
    """
    token = _extract_token(authorization)
    if not token:
        return None
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub", 0))
    except Exception:
        return None
    user = db.query(User).filter_by(id=user_id).first()
    return user if (user and user.is_active) else None
