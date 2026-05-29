from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import os

load_dotenv("backend/.env")

url = os.getenv("DATABASE_URL")
if not url:
    raise SystemExit("DATABASE_URL not found in backend/.env")

engine = create_engine(url, pool_pre_ping=True)

with engine.begin() as conn:
    user = conn.execute(
        text("SELECT id, email FROM users WHERE email = 'local@notifierr.local' OR id = 1")
    ).mappings().first()

    if not user:
        print("No placeholder user found.")
        raise SystemExit

    user_id = user["id"]
    print(f"Deleting user {user_id}: {user['email']}")

    tables = [
        "user_item_corrections",
        "user_item_states",
        "user_ignored_sellers",
        "user_ignored_keywords",
        "user_repair_value_overrides",
        "user_resale_research_overrides",
        "user_keywords",
        "user_notification_settings",
        "user_settings",
        "user_usage_daily",
    ]

    for table in tables:
        conn.execute(text(f"DELETE FROM {table} WHERE user_id = :user_id"), {"user_id": user_id})

    conn.execute(text("DELETE FROM users WHERE id = :user_id"), {"user_id": user_id})

print("Placeholder user deleted.")
