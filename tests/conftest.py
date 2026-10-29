import os
import shutil
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
FILE_ENV = dotenv_values(ROOT / '.env')
TEST_DATABASE_URL = os.getenv('TEST_DATABASE_URL') or FILE_ENV.get('TEST_DATABASE_URL')

if not TEST_DATABASE_URL:
    raise RuntimeError('TEST_DATABASE_URL is required for backend tests')

os.environ['DATABASE_URL'] = TEST_DATABASE_URL
os.environ['APP_ENV'] = 'test'
os.environ['JWT_SECRET'] = 'test-only-jwt-secret-7f3c1a9e5b2d8f4c6a0e1d3b7c9f2a5e'
os.environ['COOKIE_SECURE'] = 'false'
os.environ['LOG_LEVEL'] = 'WARNING'
os.environ['EMAIL_DELIVERY'] = 'memory'
TEST_ASSET_ROOT = Path(tempfile.mkdtemp(prefix='teachai-test-assets-'))
os.environ['ASSET_STORAGE_ROOT'] = str(TEST_ASSET_ROOT)

from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.email import clear_memory_outbox  # noqa: E402


@pytest.fixture(scope='session', autouse=True)
def migrate_database() -> None:
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('script_location', str(ROOT / 'alembic'))
    command.upgrade(config, 'head')


@pytest.fixture(autouse=True)
async def clean_database(migrate_database) -> None:
    clear_memory_outbox()
    shutil.rmtree(str(TEST_ASSET_ROOT), ignore_errors=True)
    TEST_ASSET_ROOT.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                'TRUNCATE TABLE generation_jobs, course_scenes, course_stages, '
                'course_outlines, asset_extractions, assets, courses, folders, '
                'provider_configs, auth_sessions, email_verifications, users '
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
