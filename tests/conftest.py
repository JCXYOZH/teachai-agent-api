import os
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

from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope='session', autouse=True)
def migrate_database() -> None:
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('script_location', str(ROOT / 'alembic'))
    command.upgrade(config, 'head')


@pytest.fixture(autouse=True)
async def clean_database(migrate_database) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text('TRUNCATE TABLE auth_sessions, users RESTART IDENTITY CASCADE')
        )
    yield
    await engine.dispose()


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as test_client:
        yield test_client
