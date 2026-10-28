from dataclasses import dataclass
from typing import List
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset, AssetExtraction


@dataclass(frozen=True)
class MaterialBundle:
    text: str
    asset_count: int
    char_count: int
    truncated: bool


async def load_course_materials(
    db: AsyncSession,
    owner_id: UUID,
    course_id: UUID,
    char_limit: int,
) -> MaterialBundle:
    result = await db.execute(
        select(
            Asset.filename,
            AssetExtraction.text_content,
            AssetExtraction.truncated,
        )
        .join(AssetExtraction, AssetExtraction.asset_id == Asset.id)
        .where(
            Asset.owner_id == owner_id,
            Asset.course_id == course_id,
            Asset.deleted_at.is_(None),
            Asset.extraction_status == "ready",
        )
        .order_by(Asset.created_at, Asset.id)
    )
    rows = result.all()
    remaining = char_limit
    chunks: List[str] = []
    asset_count = 0
    char_count = 0
    truncated = False
    for filename, text_content, extraction_truncated in rows:
        text = (text_content or "").strip()
        if not text:
            continue
        header = f"\n\n[Source: {filename}]\n"
        if remaining <= len(header):
            truncated = True
            break
        available = remaining - len(header)
        content = text[:available]
        if len(content) < len(text):
            truncated = True
        chunks.append(header + content)
        asset_count += 1
        char_count += len(content)
        remaining -= len(header) + len(content)
        if extraction_truncated:
            truncated = True
        if remaining <= 0:
            truncated = True
            break
    return MaterialBundle(
        text="".join(chunks).strip(),
        asset_count=asset_count,
        char_count=char_count,
        truncated=truncated,
    )
