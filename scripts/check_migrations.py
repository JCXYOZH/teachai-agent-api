import os
from pathlib import Path
from urllib.parse import urlparse

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
FILE_ENV = dotenv_values(ROOT / '.env')
TEST_DATABASE_URL = os.getenv('TEST_DATABASE_URL') or FILE_ENV.get('TEST_DATABASE_URL')

if not TEST_DATABASE_URL:
    raise SystemExit('TEST_DATABASE_URL is required')

database_name = urlparse(TEST_DATABASE_URL).path.lstrip('/')
if not database_name.endswith('_test'):
    raise SystemExit('Refusing to run migration check against a non-test database')

os.environ['DATABASE_URL'] = TEST_DATABASE_URL
config = Config(str(ROOT / 'alembic.ini'))
config.set_main_option('script_location', str(ROOT / 'alembic'))

print('upgrade head')
command.upgrade(config, 'head')
print('downgrade base')
command.downgrade(config, 'base')
print('upgrade head')
command.upgrade(config, 'head')
print('migration round-trip ok')
