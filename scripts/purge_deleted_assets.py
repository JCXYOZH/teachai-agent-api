import argparse
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.core.time import utcnow  # noqa: E402
from app.db.session import AsyncSessionLocal, engine  # noqa: E402
from app.models.asset import Asset  # noqa: E402
from app.services.storage import LocalAssetStorage  # noqa: E402


async def purge_deleted_assets(older_than_days: int, apply: bool) -> None:
    cutoff = utcnow() - timedelta(days=older_than_days)
    storage = LocalAssetStorage(settings)
    removed = 0
    missing = 0

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Asset.id, Asset.owner_id, Asset.deleted_at)
            .where(Asset.deleted_at.is_not(None), Asset.deleted_at < cutoff)
            .order_by(Asset.deleted_at)
        )
        rows = result.all()

    print(f"deleted assets older than {older_than_days} days: {len(rows)}")
    for asset_id, owner_id, deleted_at in rows:
        path = storage.asset_directory(owner_id, asset_id)
        exists = path.is_dir()
        action = "delete" if apply and exists else "would delete"
        if not exists:
            action = "missing"
            missing += 1
        print(f"{action}: {asset_id} deleted_at={deleted_at.isoformat()} path={path}")
        if apply and exists:
            if await storage.purge_asset(owner_id, asset_id):
                removed += 1

    print(f"files removed={removed} missing={missing} dry_run={not apply}")
    await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove storage directories for soft-deleted assets."
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=30,
        help="Only purge assets deleted at least this many days ago (default: 30).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete files. Without this flag the command only prints the plan.",
    )
    args = parser.parse_args()
    if args.older_than_days < 1:
        parser.error("--older-than-days must be at least 1")
    asyncio.run(purge_deleted_assets(args.older_than_days, args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
