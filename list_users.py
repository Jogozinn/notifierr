from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import os

load_dotenv("backend/.env")
engine = create_engine(os.getenv("DATABASE_URL"), pool_pre_ping=True)

with engine.connect() as conn:
    rows = conn.execute(text("SELECT id, email, role, account_status FROM users ORDER BY id")).mappings().all()

print("users:", len(rows))
for row in rows:
    print(dict(row))
