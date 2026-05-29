from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import os

load_dotenv("backend/.env")
engine = create_engine(os.getenv("DATABASE_URL"), pool_pre_ping=True)

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE"))
    conn.execute(text("CREATE SCHEMA public"))
    conn.execute(text("GRANT ALL ON SCHEMA public TO public"))

print("Neon public schema reset.")
