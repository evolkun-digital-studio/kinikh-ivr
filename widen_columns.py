import asyncio
from sqlalchemy import text
from app.db.session import get_engine
engine = get_engine()
SQL = [
  "ALTER TABLE calls ALTER COLUMN transfer_number TYPE VARCHAR(255)",
  "ALTER TABLE calls ALTER COLUMN transfer_department TYPE VARCHAR(64)",
]
async def main():
    async with engine.begin() as c:
        for s in SQL:
            await c.execute(text(s)); print("OK:", s)
asyncio.run(main())
