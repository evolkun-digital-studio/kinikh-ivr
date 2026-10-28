import asyncpg
import asyncio

async def main():
    conn = await asyncpg.connect('postgresql://kinikh:password@localhost:5432/kinikh_ivr')
    records = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'calls'")
    print([r['column_name'] for r in records])
    await conn.close()

if __name__ == '__main__':
    asyncio.run(main())
