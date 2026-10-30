import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.errors import AppError  # noqa: E402
from app.services.provider_registry import get_provider_spec  # noqa: E402
from app.services.providers import ResolvedProvider  # noqa: E402
from app.services.tts import synthesize_speech  # noqa: E402


async def run(args: argparse.Namespace, api_key: str) -> int:
    spec = get_provider_spec('qwen')
    if spec is None:
        print('qwen provider spec is missing', file=sys.stderr)
        return 1
    resolved = ResolvedProvider(
        spec=spec,
        config=None,
        api_key=api_key,
        api_key_source='environment',
        base_url=args.base_url,
        default_model='qwen-plus',
        tts_base_url=args.base_url,
        tts_model=args.model,
        tts_voice=args.voice,
    )
    try:
        audio = await synthesize_speech(
            resolved,
            args.text,
            speed=1.0,
            timeout=args.timeout,
            max_bytes=args.max_bytes,
        )
    except AppError as exc:
        print(
            json.dumps(
                {'success': False, 'code': exc.code, 'message': exc.message},
                ensure_ascii=False,
            )
        )
        return 1
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(audio.data)
    print(
        json.dumps(
            {
                'success': True,
                'format': audio.format,
                'mime_type': audio.mime_type,
                'bytes': len(audio.data),
                'duration_ms': audio.duration_ms,
                'output': str(target),
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Run a real Qwen TTS probe.')
    parser.add_argument(
        '--base-url',
        default=os.getenv('QWEN_TTS_BASE_URL', 'https://dashscope.aliyuncs.com'),
    )
    parser.add_argument('--model', default=os.getenv('QWEN_TTS_MODEL', 'qwen3-tts-flash'))
    parser.add_argument('--voice', default=os.getenv('QWEN_TTS_VOICE', 'Cherry'))
    parser.add_argument('--api-key-env', default='DASHSCOPE_API_KEY')
    parser.add_argument('--text', default='同学们好，欢迎来到今天的课堂。')
    parser.add_argument('--out', default=str(ROOT / 'tts-smoke.mp3'))
    parser.add_argument('--timeout', type=float, default=120.0)
    parser.add_argument('--max-bytes', type=int, default=20 * 1024 * 1024)
    args = parser.parse_args()

    api_key = os.getenv(args.api_key_env, '').strip()
    if not api_key:
        print(f'{args.api_key_env} is not set', file=sys.stderr)
        return 1
    return asyncio.run(run(args, api_key))


if __name__ == '__main__':
    raise SystemExit(main())
