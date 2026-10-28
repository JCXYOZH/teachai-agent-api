import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.classroom_dsl import DSL_VERSION, compile_scenes  # noqa: E402
from app.services.classroom_prompt import (  # noqa: E402
    SYSTEM_PROMPT,
    build_classroom_user_prompt,
)
from app.services.classroom_schema import normalize_classroom_plan  # noqa: E402
from app.services.llm import (  # noqa: E402
    LLMProviderError,
    LLMTimeoutError,
    call_openai_compatible,
)

SAMPLE_OUTLINE = {
    "courseTitle": "极限与连续",
    "scenes": [
        {
            "type": "slide",
            "title": "从平均变化率到瞬时变化率",
            "description": "用平均速度引出极限的直观含义。",
            "key_points": ["平均变化率", "趋近过程", "瞬时速度"],
        },
        {
            "type": "quiz",
            "title": "检查理解",
            "description": "判断当间隔趋于零时会发生什么。",
            "key_points": ["极限定义"],
        },
    ],
}


async def run(args: argparse.Namespace, api_key: str) -> int:
    prompt = build_classroom_user_prompt(
        course_title="极限与连续",
        description="面向大学新生的微积分入门课程",
        language_directive="用简体中文教学，保留必要英文术语。",
        outline=SAMPLE_OUTLINE,
        instructions=args.instructions,
        material_excerpt="",
    )
    try:
        raw = await call_openai_compatible(
            base_url=args.base_url,
            api_key=api_key,
            model=args.model,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            timeout=args.timeout,
            text_only=True,
        )
        plan = normalize_classroom_plan(raw, "极限与连续")
        scenes = compile_scenes(plan, uuid4(), "用简体中文教学。")
    except (LLMTimeoutError, LLMProviderError) as exc:
        print(
            json.dumps(
                {"success": False, "error": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1
    except ValueError as exc:
        print(
            json.dumps(
                {"success": False, "error": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1

    canvas = scenes[0]["content"].get("canvas", {})
    print(
        json.dumps(
            {
                "success": True,
                "name": plan.name,
                "scene_count": len(scenes),
                "scene_types": [scene["type"] for scene in scenes],
                "dsl_version": DSL_VERSION,
                "canvas": {
                    "viewportSize": canvas.get("viewportSize"),
                    "viewportRatio": canvas.get("viewportRatio"),
                    "background": canvas.get("background"),
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Run a real classroom generation probe.")
    parser.add_argument(
        "--base-url",
        default=os.getenv(
            "QWEN_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
    )
    parser.add_argument("--model", default=os.getenv("QWEN_MODEL", "qwen3.8-omni-flash"))
    parser.add_argument("--api-key-env", default="DASHSCOPE_API_KEY")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--instructions", default="强调直觉，并给出一个生活化例子。")
    args = parser.parse_args()

    api_key = os.getenv(args.api_key_env, "").strip()
    if not api_key:
        print(f"{args.api_key_env} is not set", file=sys.stderr)
        return 1
    return asyncio.run(run(args, api_key))


if __name__ == "__main__":
    raise SystemExit(main())
