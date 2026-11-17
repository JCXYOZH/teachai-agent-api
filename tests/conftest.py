import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
FILE_ENV = dotenv_values(ROOT / '.env')
TEST_DATABASE_URL = os.getenv('TEST_DATABASE_URL') or FILE_ENV.get('TEST_DATABASE_URL')

if not TEST_DATABASE_URL:
    raise RuntimeError('TEST_DATABASE_URL is required for backend tests')


def _worker_database_url(base_url: str) -> Tuple[str, Optional[str]]:
    """Point every xdist worker at its own database so suites can run in parallel."""
    worker = os.environ.get('PYTEST_XDIST_WORKER')
    if not worker:
        return base_url, None
    url = make_url(base_url)
    name = f"{url.database}_{worker}"
    return url.set(database=name).render_as_string(hide_password=False), name


TEST_DATABASE_URL, WORKER_DATABASE = _worker_database_url(TEST_DATABASE_URL)


async def _ensure_worker_database() -> None:
    if not WORKER_DATABASE:
        return
    admin = make_url(TEST_DATABASE_URL).set(drivername='postgresql', database='postgres')
    connection = await asyncpg.connect(admin.render_as_string(hide_password=False))
    try:
        exists = await connection.fetchval(
            'SELECT 1 FROM pg_database WHERE datname = $1', WORKER_DATABASE
        )
        if not exists:
            try:
                await connection.execute(f'CREATE DATABASE "{WORKER_DATABASE}"')
            except asyncpg.exceptions.DuplicateDatabaseError:
                pass
    finally:
        await connection.close()


def _provision_worker_database() -> None:
    """Create the per-worker database on a throwaway loop before migrations run."""
    if not WORKER_DATABASE:
        return
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_ensure_worker_database())
    finally:
        loop.close()

os.environ['DATABASE_URL'] = TEST_DATABASE_URL
os.environ['APP_ENV'] = 'test'
os.environ['JWT_SECRET'] = 'test-only-jwt-secret-7f3c1a9e5b2d8f4c6a0e1d3b7c9f2a5e'
os.environ['COOKIE_SECURE'] = 'false'
os.environ['LOG_LEVEL'] = 'WARNING'
os.environ['EMAIL_DELIVERY'] = 'memory'
os.environ['SMS_DELIVERY'] = 'memory'
# Keeps tests offline: the local .env may enable the optional VLM scorer.
os.environ['IMAGE_QUALITY_VLM_ENABLED'] = 'false'
# Fallback behaviour is exercised by tests, so pin it regardless of .env.
os.environ['IMAGE_FALLBACK_CHAIN'] = 'pollinations'
# Tests run offline; never wait on the free-tier spacing gate.
os.environ['IMAGE_POLLINATIONS_MIN_INTERVAL_SECONDS'] = '0'
# Tests drive the BackgroundTasks path; the worker pool needs a lifespan.
os.environ['GENERATION_QUEUE_ENABLED'] = 'false'
TEST_ASSET_ROOT = Path(tempfile.mkdtemp(prefix='teachai-test-assets-'))
os.environ['ASSET_STORAGE_ROOT'] = str(TEST_ASSET_ROOT)

from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.email import clear_memory_outbox  # noqa: E402
from app.services.sms import clear_memory_outbox as clear_sms_outbox  # noqa: E402


@pytest.fixture(scope='session', autouse=True)
def migrate_database() -> None:
    _provision_worker_database()
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('script_location', str(ROOT / 'alembic'))
    command.upgrade(config, 'head')


@pytest.fixture(autouse=True)
async def clean_database(migrate_database) -> None:
    clear_memory_outbox()
    clear_sms_outbox()
    shutil.rmtree(str(TEST_ASSET_ROOT), ignore_errors=True)
    TEST_ASSET_ROOT.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                'TRUNCATE TABLE media_assets, generation_jobs, course_scenes, course_stages, '
                'course_outlines, asset_extractions, assets, courses, folders, '
                'provider_configs, auth_sessions, email_verifications, phone_verifications, '
                'verification_send_logs, users '
                'RESTART IDENTITY CASCADE'
            )
        )
    yield
    clear_memory_outbox()
    shutil.rmtree(str(TEST_ASSET_ROOT), ignore_errors=True)
    await engine.dispose()


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as test_client:
        yield test_client
