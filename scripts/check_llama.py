import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402


def main() -> int:
    url = settings.llama_base_url.rstrip('/') + '/models'
    headers = {'Authorization': f'Bearer {settings.llama_api_key}'}
    try:
        response = httpx.get(url, headers=headers, timeout=8.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f'llama.cpp is not reachable: {type(exc).__name__}', file=sys.stderr)
        return 1

    payload = response.json()
    models = [item.get('id', 'unknown') for item in payload.get('data', [])]
    print(f'llama.cpp ok models={models}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
