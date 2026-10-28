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
      extracted.txt
      metadata.json
```

Storage paths use generated UUIDs, never user-supplied filenames. Original filenames remain metadata only. This prevents path traversal and collisions between owners.

## Database Metadata

The asset table will record:

- `id`, `owner_id`, optional `course_id`
- original filename, detected media type and byte size
- SHA-256 digest
- relative storage key and extraction status
- created, updated and deleted timestamps

## Access and Writes

- Every asset read and write is scoped by `owner_id`.
- Downloads are streamed through authenticated API routes; the storage root is never public.
- Writes go to a temporary file first and are atomically renamed after validation.
- Deletion is soft at the database level so an accidental course deletion does not immediately destroy source material.
- Upload size limits, MIME validation, file signature checks and parser timeouts will be enforced in Phase 2.

## Operational Checks

Phase 2 will add readiness checks for:

- storage root existence and write access
- available disk space
- last write test timestamp

`pgvector` is not used. Text retrieval remains PostgreSQL-based and does not depend on this storage design.
