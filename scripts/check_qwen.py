import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.providers import _probe_openai  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description='Run a real Qwen chat completion probe.')
    parser.add_argument(
        '--base-url',
        default=os.getenv('QWEN_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1'),
    )
    parser.add_argument('--model', default=os.getenv('QWEN_MODEL', 'qwen3.8-omni-flash'))
    parser.add_argument('--api-key-env', default='DASHSCOPE_API_KEY')
    parser.add_argument('--timeout', type=float, default=60.0)
    args = parser.parse_args()

    api_key = os.getenv(args.api_key_env, '').strip()
    if not api_key:
        print(f'{args.api_key_env} is not set', file=sys.stderr)
        return 1

    result = asyncio.run(
        _probe_openai(
            args.base_url,
            api_key,
            args.model,
            args.timeout,
            text_only=True,
        )
    )
    print(
        json.dumps(
            {
                'success': result.success,
                'message': result.message,
                'model': result.model,
                'reported_models': result.models[:10],
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.success else 1


if __name__ == '__main__':
    raise SystemExit(main())
