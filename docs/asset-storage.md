# Asset Storage

Uploaded course assets use local disk storage. PostgreSQL stores metadata and ownership; it does not store file bytes.

## Location

The default root is `E:\Backups\Data\TeachAIAgent` and is configurable with `ASSET_STORAGE_ROOT`.

- The API process must have read and write access to the root.
- The root must be an absolute path and must not be served through `/static`.
- Docker deployments must mount persistent storage and set `ASSET_STORAGE_ROOT` to the mounted path.
- Backups must include both PostgreSQL and this directory.

## Layout

```text
<root>/
  <owner_id>/
    <asset_id>/
      original<extension>
```

Storage paths use generated UUIDs, never user-supplied filenames. Original filenames remain metadata only. This prevents path traversal and collisions between owners.

## Database Metadata

The asset table records:

- `id`, `owner_id`, optional `course_id`
- original filename, detected media type and byte size
- SHA-256 digest
- relative storage key and extraction status
- created, updated and deleted timestamps

Extracted text and source sections are stored in `asset_extractions`, not as sidecar files.

## Access and Writes

- Every asset read and write is scoped by `owner_id`.
- Downloads are streamed through authenticated API routes; the storage root is never public.
- Writes go to a temporary file first and are atomically renamed after validation.
- Deletion is soft at the database level so an accidental course deletion does not immediately destroy source material.
- Upload size, extension, PDF signature, Office archive structure, encrypted-document checks and parser timeouts are enforced by the API.
- PDFs are split by page, DOCX includes paragraphs and tables, PPTX includes slides and notes, and text files are split into line sections.
- Text uploads support UTF-8, GB18030 and UTF-16 with a byte-order mark.
- Chinese search uses PostgreSQL `ILIKE` and `strpos`; `pg_trgm` is optional and `pgvector` is not used.

## Deleted Files

Soft-deleted assets stay on disk until an operator explicitly purges them. The cleanup command is a dry run by default:

```powershell
.\.venv\Scripts\python.exe scripts\purge_deleted_assets.py --older-than-days 30
```

After reviewing the listed directories, run the same command with `--apply`. The command removes only the asset storage directory; database metadata remains available for audit.

## Operational Checks

The readiness endpoint checks:

- storage root existence and write access
- available disk space
- a short write probe that is removed immediately after the check

`pgvector` is not used. Text retrieval remains PostgreSQL-based and does not depend on this storage design.
