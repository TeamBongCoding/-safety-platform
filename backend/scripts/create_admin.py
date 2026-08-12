"""서버 터미널에서만 실행하는 최초 관리자 생성 도구."""
import getpass
import sys
from pathlib import Path

# `python scripts/create_admin.py`로 실행해도 backend/app을 찾을 수 있게 한다.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select

from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app.migrations import migrate_legacy_schema
from app.models import User


def main():
    migrate_legacy_schema(engine)
    Base.metadata.create_all(bind=engine)

    email = input("관리자 이메일: ").strip().lower()
    manager_name = input("관리자명 [시스템 관리자]: ").strip() or "시스템 관리자"
    password = getpass.getpass("비밀번호(8자 이상): ")
    confirm = getpass.getpass("비밀번호 확인: ")

    if "@" not in email:
        raise SystemExit("올바른 이메일을 입력하세요.")
    if len(password) < 8:
        raise SystemExit("비밀번호는 8자 이상이어야 합니다.")
    if password != confirm:
        raise SystemExit("비밀번호가 일치하지 않습니다.")

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user:
            user.role = "platform_admin"
            user.status = "active"
            user.suspended_at = None
            user.password_hash = hash_password(password)
            user.manager_name = manager_name
            print(f"기존 계정을 서버 관리자로 변경했습니다: {email}")
        else:
            db.add(User(
                email=email,
                password_hash=hash_password(password),
                company_name="시스템 관리",
                manager_name=manager_name,
                role="platform_admin",
                status="active",
            ))
            print(f"서버 관리자 계정을 생성했습니다: {email}")
        db.commit()


if __name__ == "__main__":
    main()
