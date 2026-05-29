from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from getpass import getpass
from datetime import datetime, timezone
import importlib
import os

load_dotenv("backend/.env")

url = os.getenv("DATABASE_URL")
if not url:
    raise SystemExit("DATABASE_URL not found in backend/.env")

auth = importlib.import_module("backend.auth")

hash_fn = None
for name in ("hash_password", "get_password_hash", "hash_user_password"):
    fn = getattr(auth, name, None)
    if callable(fn):
        hash_fn = fn
        print(f"Using backend.auth.{name}")
        break

if hash_fn is None:
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    hash_fn = pwd_context.hash
    print("Using fallback passlib bcrypt hasher")

email = input("New admin email: ").strip().lower()
display_name = input("Display name [Ryan]: ").strip() or "Ryan"
password = getpass("New admin password: ")
confirm = getpass("Confirm password: ")

if not email:
    raise SystemExit("Email required")
if password != confirm:
    raise SystemExit("Passwords do not match")
if len(password) < 8:
    raise SystemExit("Use at least 8 characters")

password_hash = hash_fn(password)

engine = create_engine(url, pool_pre_ping=True)

with engine.begin() as conn:
    result = conn.execute(
        text("""
            UPDATE users
            SET email = :email,
                display_name = :display_name,
                password_hash = :password_hash,
                role = 'admin',
                account_status = 'active',
                updated_at = :updated_at
            WHERE email = 'local@notifierr.local'
               OR id = 1
        """),
        {
            "email": email,
            "display_name": display_name,
            "password_hash": password_hash,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    if result.rowcount == 0:
        raise SystemExit("No local/admin user found to update")

print("Admin account updated successfully.")
