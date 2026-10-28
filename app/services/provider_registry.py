from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    description: str
    protocol: str
    default_base_url: str
    alternate_base_urls: Tuple[str, ...]
    requires_api_key: bool
    supports_model_discovery: bool
    default_models: Tuple[str, ...]
    env_key_setting: str
    env_key_aliases: Tuple[str, ...] = ()
    env_base_url_setting: Optional[str] = None
    env_model_setting: Optional[str] = None


PROVIDER_SPECS: Dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        id="openai",
        label="OpenAI",
        description="OpenAI official API and compatible production endpoints.",
        protocol="openai",
        default_base_url="https://api.openai.com/v1",
        alternate_base_urls=(),
        requires_api_key=True,
        supports_model_discovery=True,
        default_models=("gpt-4o-mini", "gpt-4o"),
        env_key_setting="openai_api_key",
    ),
    "anthropic": ProviderSpec(
        id="anthropic",
        label="Anthropic",
        description="Claude models through the native Messages API.",
        protocol="anthropic",
        default_base_url="https://api.anthropic.com/v1",
        alternate_base_urls=(),
        requires_api_key=True,
        supports_model_discovery=False,
        default_models=("claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"),
        env_key_setting="anthropic_api_key",
    ),
    "deepseek": ProviderSpec(
        id="deepseek",
        label="DeepSeek",
        description="DeepSeek official OpenAI-compatible API.",
        protocol="openai",
        default_base_url="https://api.deepseek.com/v1",
        alternate_base_urls=(),
        requires_api_key=True,
        supports_model_discovery=True,
        default_models=("deepseek-chat", "deepseek-reasoner"),
        env_key_setting="deepseek_api_key",
    ),
    "qwen": ProviderSpec(
        id="qwen",
        label="Qwen / DashScope",
        description="Alibaba Cloud DashScope or a custom Qwen-compatible gateway.",
        protocol="openai",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alternate_base_urls=(),
        requires_api_key=True,
        supports_model_discovery=True,
        default_models=("qwen-plus", "qwen-max", "qwen-turbo", "qwen3.8-omni-flash"),
        env_key_setting="qwen_api_key",
        env_key_aliases=("dashscope_api_key",),
    ),
    "kimi": ProviderSpec(
        id="kimi",
        label="Kimi / Moonshot",
        description="Moonshot AI China or international OpenAI-compatible API.",
        protocol="openai",
        default_base_url="https://api.moonshot.cn/v1",
        alternate_base_urls=("https://api.moonshot.ai/v1",),
        requires_api_key=True,
        supports_model_discovery=True,
        default_models=("moonshot-v1-8k", "moonshot-v1-32k"),
        env_key_setting="kimi_api_key",
    ),
    "glm": ProviderSpec(
        id="glm",
        label="GLM / Zhipu",
        description="Zhipu AI China or Z.ai international OpenAI-compatible API.",
        protocol="openai",
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        alternate_base_urls=("https://api.z.ai/api/paas/v4",),
        requires_api_key=True,
        supports_model_discovery=True,
        default_models=("glm-4-flash", "glm-4-plus"),
        env_key_setting="glm_api_key",
    ),
    "llama-local": ProviderSpec(
        id="llama-local",
        label="Local llama.cpp",
        description="The local llama-server OpenAI-compatible endpoint.",
        protocol="openai",
        default_base_url="http://127.0.0.1:65535/v1",
        alternate_base_urls=(),
        requires_api_key=False,
        supports_model_discovery=True,
        default_models=("Qwen3.6-35B-A3B",),
        env_key_setting="llama_api_key",
        env_base_url_setting="llama_base_url",
        env_model_setting="llama_model",
    ),
}


def get_provider_spec(provider_id: str) -> Optional[ProviderSpec]:
    return PROVIDER_SPECS.get(provider_id)
