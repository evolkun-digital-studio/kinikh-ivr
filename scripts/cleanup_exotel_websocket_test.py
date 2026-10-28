"""Delete the Call/Lead row(s) created by scripts/test_exotel_websocket.py.

Uses the app's own async session factory (same DATABASE_URL the running
backend uses), so this always targets the real database the test actually
wrote to. Only ever deletes rows whose call_sid starts with the unmistakable
"TEST_PROTOCOL_VERIFY_" prefix — refuses to touch anything else.

Usage:
    python scripts/cleanup_exotel_websocket_test.py TEST_PROTOCOL_VERIFY_ABCD1234
    python scripts/cleanup_exotel_websocket_test.py --all   # delete every
        row matching the TEST_PROTOCOL_VERIFY_ prefix
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.db.session import close_engine, get_session_factory
from app.models.call import Call
from app.models.lead import Lead

PREFIX = "TEST_PROTOCOL_VERIFY_"


async def cleanup(call_sid: str | None) -> None:
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(Call)
        if call_sid:
            stmt = stmt.where(Call.call_sid == call_sid)
        else:
            stmt = stmt.where(Call.call_sid.like(f"{PREFIX}%"))
        result = await session.execute(stmt)
        calls = list(result.scalars())

        if not calls:
            print(f"No matching test Call rows found (filter={call_sid or PREFIX + '%'}).")
            return

        for call in calls:
            lead_result = await session.execute(select(Lead).where(Lead.call_id == call.id))
            for lead in lead_result.scalars():
                print(f"Deleting Lead id={lead.id} call_id={call.id} phone={lead.phone}")
                await session.delete(lead)

            print(f"Deleting Call id={call.id} call_sid={call.call_sid} status={call.status}")
            await session.delete(call)

        await session.commit()
        print(f"Cleaned up {len(calls)} Call row(s).")


async def _run(call_sid: str | None) -> None:
    # Both calls must share one event loop — asyncpg connections are bound
    # to the loop that created them, so disposing the engine from a second
    # asyncio.run() loop hangs indefinitely instead of closing cleanly.
    await cleanup(call_sid)
    await close_engine()


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    arg = sys.argv[1]
    call_sid = None if arg == "--all" else arg
    if call_sid and not call_sid.startswith(PREFIX):
        print(f"Refusing to delete: '{call_sid}' does not start with required prefix '{PREFIX}'.")
        sys.exit(1)

    asyncio.run(_run(call_sid))


if __name__ == "__main__":
    main()
