from typing import Any, cast
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select
from support import register_and_verify

from app.core.config import settings
from app.core.errors import AppError
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.services.image_vlm import VlmScore, parse_score, score_illustration
from app.services.providers import (
    ProviderConfigService,
    ResolvedProvider,
    get_provider_spec,
)


def test_parse_score_reads_json_payload() -> None:
    parsed = parse_score(
        '{"score": 82, "issues": ["has text", "  flat   layout "]}', model="m"
    )
    assert parsed == VlmScore(score=82, issues=["has text", "flat layout"], model="m")


def test_parse_score_reads_fenced_json() -> None:
    parsed = parse_score('```json\n{"score": 71}\n```', model="m")
    assert parsed is not None
    assert parsed.score == 71
    assert parsed.issues == []


def test_parse_score_reads_plain_number() -> None:
    parsed = parse_score("76 / 100", model="m")
    assert parsed is not None
    assert parsed.score == 76


def test_parse_score_rejects_unhelpful_text() -> None:
    assert parse_score("The image looks fine to me.") is None
    assert parse_score("   ") is None


def test_parse_score_clamps_and_limits_issues() -> None:
    high = parse_score('{"score": 140, "issues": []}')
    assert high is not None
    assert high.score == 100
    low = parse_score('{"score": -3}')
    assert low is not None
    assert low.score == 0
    many = parse_score('{"score": 50, "issues": ["a", "b", "c", "d", "e", "f"]}')
    assert many is not None
    assert len(many.issues) == 5
    long_issue = parse_score('{"score": 50, "issues": ["' + "x" * 200 + '"]}')
    assert long_issue is not None
    assert len(long_issue.issues[0]) == 120


async def test_vlm_scoring_disabled_returns_none(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "image_quality_vlm_enabled", False)
    result = await score_illustration(
        db=cast(Any, None),
        settings=settings,
        owner=cast(Any, None),
        image=b"",
        prompt="topic",
    )
    assert result is None


async def test_vlm_scoring_skips_unconfigured_provider(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "image_quality_vlm_enabled", True)

    async def fail(
        self: ProviderConfigService, owner: User, provider_id: Any = None
    ) -> ResolvedProvider:
        raise AppError(422, "PROVIDER_NOT_CONFIGURED", "missing credential")

    monkeypatch.setattr(ProviderConfigService, "resolve_for_generation", fail)
    result = await score_illustration(
        db=cast(Any, None),
        settings=settings,
        owner=cast(Any, None),
        image=b"x",
        prompt="topic",
    )
    assert result is None


async def test_vlm_scoring_skips_user_without_credential(
    client: AsyncClient, monkeypatch: Any
) -> None:
    registered = await register_and_verify(client, "vlm-owner@example.com")
    owner_id = registered.json()["user"]["id"]
    async with AsyncSessionLocal() as db:
        owner = await db.scalar(select(User).where(User.id == UUID(owner_id)))
        assert owner is not None
        monkeypatch.setattr(settings, "image_quality_vlm_enabled", True)

        async def no_credential(
            self: ProviderConfigService, owner: User, provider_id: Any = None
        ) -> ResolvedProvider:
            raise AppError(422, "PROVIDER_NOT_CONFIGURED", "missing credential")

        monkeypatch.setattr(ProviderConfigService, "resolve_for_generation", no_credential)
        result = await score_illustration(
            db=db,
            settings=settings,
            owner=owner,
            image=b"RIFF....WEBP",
            prompt="photosynthesis",
        )
    assert result is None


class _FakeResponse:
    status_code = 200

    def json(self) -> dict:
        return {
            "choices": [
                {"message": {"content": '{"score": 41, "issues": ["watermark"]}'}}
            ]
        }


class _FakeClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def post(
        self, url: str, headers: Any = None, json: Any = None
    ) -> _FakeResponse:
        assert url == "https://example.test/v1/chat/completions"
        assert headers["Authorization"] == "Bearer sk-test-key"
        assert json["model"] == settings.image_quality_vlm_model
        return _FakeResponse()


async def test_vlm_scoring_parses_model_reply(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "image_quality_vlm_enabled", True)
    spec = get_provider_spec("qwen")
    assert spec is not None

    async def resolve(
        self: ProviderConfigService, owner: User, provider_id: Any = None
    ) -> ResolvedProvider:
        return ResolvedProvider(
            spec=spec,
            config=None,
            api_key="sk-test-key",
            api_key_source="user",
            base_url="https://example.test/v1",
            default_model=settings.image_quality_vlm_model,
        )

    monkeypatch.setattr(ProviderConfigService, "resolve_for_generation", resolve)
    monkeypatch.setattr("app.services.image_vlm.httpx.AsyncClient", _FakeClient)
    result = await score_illustration(
        db=cast(Any, None),
        settings=settings,
        owner=cast(Any, None),
        image=b"RIFF....WEBP",
        prompt="photosynthesis",
    )
    assert result == VlmScore(
        score=41, issues=["watermark"], model=settings.image_quality_vlm_model
    )
