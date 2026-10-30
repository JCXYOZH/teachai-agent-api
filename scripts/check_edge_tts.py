import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.errors import AppError  # noqa: E402
from app.services.provider_registry import EDGE_TTS_SPEC  # noqa: E402
from app.services.providers import ResolvedProvider  # noqa: E402
from app.services.tts import synthesize_speech  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    resolved = ResolvedProvider(
        spec=EDGE_TTS_SPEC,
        config=None,
        api_key=None,
        api_key_source=None,
        base_url="",
        default_model="edge-tts",
        tts_model="edge-tts",
        tts_voice=args.voice,
    )
    try:
        audio = await synthesize_speech(
            resolved,
            args.text,
            speed=args.speed,
            timeout=args.timeout,
            max_bytes=args.max_bytes,
        )
    except AppError as exc:
        print(
            json.dumps(
                {"success": False, "code": exc.code, "message": exc.message},
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
                "success": True,
                "format": audio.format,
                "mime_type": audio.mime_type,
                "bytes": len(audio.data),
                "duration_ms": audio.duration_ms,
                "voice": args.voice,
                "output": str(target),
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real Edge TTS probe.")
    parser.add_argument("--voice", default="zh-CN-XiaoxiaoNeural")
    parser.add_argument(
        "--text",
        default="同学们好，欢迎来到今天的课堂。我们先回顾上节课的知识，然后进入今天的学习主题。",
    )
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--out", default=str(ROOT / "edge-tts-smoke.mp3"))
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-bytes", type=int, default=20 * 1024 * 1024)
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
