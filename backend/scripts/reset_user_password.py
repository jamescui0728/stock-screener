"""
重置某个用户的登录密码（管理员自己也被锁在外面时的救援入口）。

为什么需要它：
  - `/api/auth/users/{id}/reset-password` 要管理员 token —— 管理员自己进不去时是死循环
  - `migrate_add_users.py` 的 create_admin 遇到已存在的用户会「跳过创建」，不重置密码

密码从命令行传入，不写进任何文件，也不落日志（打印时一律打码）。
哈希复用 auth.hash_password（bcrypt，自动盐 + cost 12），不另造一套。

使用：
  .venv/bin/python scripts/reset_user_password.py --phone 13812345678 --password '新密码'
      # dry-run：只打印会改谁，不写库
  .venv/bin/python scripts/reset_user_password.py --phone 13812345678 --password '新密码' --apply
      # 实际写库
"""
import argparse
import sys

sys.path.insert(0, ".")

from auth import hash_password, normalize_phone, verify_password  # noqa: E402
from database import SessionLocal  # noqa: E402
from models.models import User  # noqa: E402

MIN_LEN = 6


def _mask(pw: str) -> str:
    """打印用：只露首尾各一位，中间打码。避免密码进终端历史 / 日志。"""
    if len(pw) <= 2:
        return "*" * len(pw)
    return f"{pw[0]}{'*' * (len(pw) - 2)}{pw[-1]}"


def main(phone: str, password: str, apply: bool):
    phone = normalize_phone(phone)

    if len(password) < MIN_LEN:
        print(f"✘ 密码太短（至少 {MIN_LEN} 位），已中止。")
        sys.exit(2)
    # bcrypt 只取前 72 字节，超出部分会被静默忽略 —— 与其让用户以为设了个长密码，不如直接拒绝
    if len(password.encode("utf-8")) > 72:
        print("✘ 密码超过 72 字节（bcrypt 上限，超出部分会被静默忽略），请换短一点的。")
        sys.exit(2)

    db = SessionLocal()
    try:
        user = db.query(User).filter_by(phone=phone).first()
        if not user:
            print(f"✘ 找不到手机号为 {phone} 的用户。")
            print("  现有用户：")
            for u in db.query(User).order_by(User.id).all():
                print(f"    id={u.id}  phone={u.phone}  name={u.name}")
            sys.exit(2)

        already = verify_password(password, user.password_hash)

        print("=" * 60)
        print("重置计划：")
        print(f"  用户 id={user.id}  phone={user.phone}  name={user.name}")
        print(f"  管理员={user.is_admin}  启用={user.is_active}")
        print(f"  新密码={_mask(password)}（{len(password)} 位）")
        if already:
            print("  · 注意：这个密码与当前哈希已经匹配，重置后等于没变")
        if not user.is_active:
            print("  ⚠ 该账户已被禁用，重置密码后仍然登录不了（登录会返回 403）")
        print("=" * 60)

        if not apply:
            print("\n[dry-run] 未写库。加 --apply 实际执行。")
            return

        user.password_hash = hash_password(password)
        db.commit()

        # 立刻自证：重新读一遍库，用新密码验一次，确认真的写进去了
        db.refresh(user)
        if not verify_password(password, user.password_hash):
            print("\n✘ 写库后校验失败，密码未生效，请检查。")
            sys.exit(1)
        print(f"\n✓ 已重置 id={user.id}（{user.phone}）的密码，校验通过，可以登录了。")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="重置指定用户的登录密码")
    ap.add_argument("--phone", required=True, help="目标用户手机号")
    ap.add_argument("--password", required=True, help="新密码（至少 6 位）")
    ap.add_argument("--apply", action="store_true", help="实际写库；不加则只 dry-run")
    args = ap.parse_args()
    main(args.phone, args.password, args.apply)
