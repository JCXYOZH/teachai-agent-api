import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.services.images import (  # noqa: E402
    CLOUDFLARE_PROVIDER_ID,
    HUGGINGFACE_PROVIDER_ID,
    POLLINATIONS_PROVIDER_ID,
    QWEN_PROVIDER_ID,
    generate_cloudflare_image,
    generate_huggingface_image,
    generate_pollinations_image,
    generate_qwen_image,
)
from app.services.provider_registry import get_provider_spec  # noqa: E402
from app.services.providers import ResolvedProvider  # noqa: E402

PROBE_PROVIDERS = (
    QWEN_PROVIDER_ID,
    POLLINATIONS_PROVIDER_ID,
    HUGGINGFACE_PROVIDER_ID,
    CLOUDFLARE_PROVIDER_ID,
)


def _success(result: Any, target: Path) -> Dict[str, Any]:
    target.write_bytes(result.data)
    return {
        'success': True,
        'provider': result.provider_id,
        'model': result.model,
        'width': result.width,
        'height': result.height,
        'bytes': len(result.data),
        'format': 'webp',
        'output': str(target),
    }


def _failure(provider_id: str, exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, AppError):
        return {
            'success': False,
            'provider': provider_id,
            'code': exc.code,
            'message': exc.message,
        }
    return {
        'success': False,
        'provider': provider_id,
        'code': type(exc).__name__,
        'message': str(exc)[:240],
    }


async def probe_qwen(args: argparse.Namespace, api_key: str) -> Dict[str, Any]:
    spec = get_provider_spec(QWEN_PROVIDER_ID)
    if spec is None:
        return {
            'success': False,
            'provider': QWEN_PROVIDER_ID,
            'message': 'qwen provider spec is missing',
        }
    resolved = ResolvedProvider(
        spec=spec,
        config=None,
        api_key=api_key,
        api_key_source='environment',
        base_url=args.base_url,
        default_model=args.model,
    )
    result = await generate_qwen_image(
        resolved,
        prompt=args.prompt,
        model=args.model,
        timeout=args.timeout,
        max_bytes=args.max_bytes,
        max_width=args.max_width,
        max_height=args.max_height,
        webp_quality=args.quality,
    )
    return _success(result, Path(args.out_dir) / 'qwen.webp')


async def probe_pollinations(args: argparse.Namespace) -> Dict[str, Any]:
    result = await generate_pollinations_image(
        prompt=args.prompt,
        model=args.pollinations_model,
        base_url=settings.image_pollinations_base_url,
        width=args.width,
        height=args.height,
        timeout=args.timeout,
        max_bytes=args.max_bytes,
        max_width=args.max_width,
        max_height=args.max_height,
        webp_quality=args.quality,
    )
    return _success(result, Path(args.out_dir) / 'pollinations.webp')


async def probe_huggingface(args: argparse.Namespace) -> Dict[str, Any]:
    result = await generate_huggingface_image(
        endpoint=settings.image_hf_endpoint,
        model=settings.image_hf_model,
        token=settings.image_hf_token,
        prompt=args.prompt,
        width=args.width,
        height=args.height,
        timeout=args.timeout,
        max_bytes=args.max_bytes,
        max_width=args.max_width,
        max_height=args.max_height,
        webp_quality=args.quality,
    )
    return _success(result, Path(args.out_dir) / 'huggingface.webp')


async def probe_cloudflare(args: argparse.Namespace) -> Dict[str, Any]:
    result = await generate_cloudflare_image(
        account_id=settings.image_cloudflare_account_id,
        token=settings.image_cloudflare_api_token,
        model=settings.image_cloudflare_model,
        prompt=args.prompt,
        timeout=args.timeout,
        max_bytes=args.max_bytes,
        max_width=args.max_width,
        max_height=args.max_height,
        webp_quality=args.quality,
    )
    return _success(result, Path(args.out_dir) / 'cloudflare.webp')


def _api_key(args: argparse.Namespace) -> str:
    return os.getenv(args.api_key_env, '').strip() or settings.qwen_api_key.strip()


async def run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    probes: Dict[str, Callable[[], Awaitable[Dict[str, Any]]]] = {
        QWEN_PROVIDER_ID: lambda: probe_qwen(args, _api_key(args)),
        POLLINATIONS_PROVIDER_ID: lambda: probe_pollinations(args),
        HUGGINGFACE_PROVIDER_ID: lambda: probe_huggingface(args),
        CLOUDFLARE_PROVIDER_ID: lambda: probe_cloudflare(args),
    }
    selected: List[str] = (
        list(PROBE_PROVIDERS) if args.provider == 'all' else [args.provider]
    )
    results: List[Dict[str, Any]] = []
    for provider_id in selected:
        if provider_id == QWEN_PROVIDER_ID and not _api_key(args):
            results.append(
                {
                    'success': False,
                    'provider': provider_id,
                    'code': 'MISSING_API_KEY',
                    'message': f'{args.api_key_env} is not set',
                }
            )
            continue
        try:
            results.append(await probes[provider_id]())
        except Exception as exc:  # noqa: BLE001 - probe reports failures as data
            results.append(_failure(provider_id, exc))

    print(json.dumps({'results': results}, ensure_ascii=False, indent=2))
    return 0 if any(item.get('success') for item in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description='Run real image provider probes.')
    parser.add_argument('--provider', choices=(*PROBE_PROVIDERS, 'all'), default='all')
    parser.add_argument(
        '--base-url',
        default=os.getenv(
            'QWEN_IMAGE_BASE_URL',
            os.getenv('QWEN_TTS_BASE_URL', 'https://dashscope.aliyuncs.com'),
        ),
    )
    parser.add_argument(
        '--model', default=os.getenv('QWEN_IMAGE_MODEL', settings.image_model)
    )
    parser.add_argument('--api-key-env', default='DASHSCOPE_API_KEY')
    parser.add_argument(
        '--prompt',
        default='16:9 教学插画：深墨绿与暖赭红的编辑手绘质感，课堂里的学生在讨论模型训练。',
    )
    parser.add_argument('--out-dir', default=str(ROOT / 'image-smoke'))
    parser.add_argument('--width', type=int, default=settings.image_target_width)
    parser.add_argument('--height', type=int, default=settings.image_target_height)
    parser.add_argument('--max-width', type=int, default=settings.image_max_width)
    parser.add_argument('--max-height', type=int, default=settings.image_max_height)
    parser.add_argument('--quality', type=int, default=settings.image_webp_quality)
    parser.add_argument('--max-bytes', type=int, default=settings.image_max_download_bytes)
    parser.add_argument('--timeout', type=float, default=settings.image_generation_timeout_seconds)
    parser.add_argument(
        '--pollinations-model', default=settings.image_pollinations_model
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == '__main__':
    raise SystemExit(main())