from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import os
import urllib.error
import urllib.request


class ProviderError(RuntimeError):
    pass


@dataclass
class ModelConfig:
    provider: str = "mock"
    model_name: str = "mock-local"
    api_key: str | None = None
    base_url: str | None = None

    @property
    def is_openai_compatible(self) -> bool:
        return self.provider in {"openai_compatible", "openai-compatible", "openai"}

    @property
    def configured(self) -> bool:
        if self.is_openai_compatible:
            return bool(self.api_key and self.base_url and self.model_name)
        return self.provider == "mock"


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    env_path = Path(path)
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


def load_model_config() -> ModelConfig:
    file_values = load_dotenv()

    def pick(*names: str, default: str | None = None) -> str | None:
        for name in names:
            if name in file_values and file_values[name]:
                return file_values[name]
        for name in names:
            value = os.environ.get(name)
            if value:
                return value
        return default

    return ModelConfig(
        provider=(pick("HARNESS_MODEL_PROVIDER", "MODEL_PROVIDER", default="mock") or "mock").strip(),
        model_name=(pick("HARNESS_MODEL_NAME", "MODEL_NAME", default="mock-local") or "mock-local").strip(),
        api_key=pick("HARNESS_OPENAI_API_KEY", "OPENAI_API_KEY"),
        base_url=pick("HARNESS_OPENAI_BASE_URL", "OPENAI_BASE_URL"),
    )


class OpenAICompatibleClient:
    def __init__(self, config: ModelConfig) -> None:
        if not config.configured or not config.is_openai_compatible:
            raise ProviderError("OpenAI-compatible provider is not fully configured")
        self.config = config
        self.base_url = (config.base_url or "").rstrip("/")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                text = response.read().decode("utf-8")
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(f"HTTP {exc.code} for {path}: {body}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"Connection error for {path}: {exc}") from exc

    def list_models(self) -> list[str]:
        payload = self._request("GET", "/models")
        return [item.get("id", "") for item in payload.get("data", [])]

    def chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
        payload = {
            "model": self.config.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        response = self._request("POST", "/chat/completions", payload)
        choices = response.get("choices") or []
        if not choices:
            raise ProviderError("No choices returned from chat completion")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [part.get("text", "") for part in content if isinstance(part, dict)]
            return "".join(parts).strip()
        raise ProviderError("Unsupported chat completion content format")


def build_report_markdown(topic: str, facts: list[str], client: OpenAICompatibleClient | None = None) -> str:
    if client is None:
        lines = [f"# Report: {topic}", "", "## Key Findings", ""]
        if not facts:
            lines.append("- No facts were extracted from the provided sources.")
        else:
            lines.extend([f"- {fact}" for fact in facts])
        lines.extend(["", "## Harness Notes", "", "- This report was generated via a checkpointed, approval-gated local harness demo."])
        return "\n".join(lines)

    facts_block = "\n".join(f"- {fact}" for fact in facts) if facts else "- No facts were extracted from the provided sources."
    system_prompt = (
        "You are a precise engineering writing assistant. "
        "Write concise markdown only. Do not invent facts. Use only the supplied facts."
    )
    user_prompt = (
        f"Write a short markdown report for the topic: {topic}\n\n"
        "Requirements:\n"
        "- Start with '# Report: <topic>'\n"
        "- Include a '## Key Findings' section\n"
        "- Preserve factual content from the bullets below\n"
        "- Include a '## Harness Notes' section ending with one bullet that says the report was generated via a checkpointed, approval-gated local harness demo\n"
        "- Do not mention any external facts not present below\n\n"
        f"Facts:\n{facts_block}\n"
    )
    return client.chat(system_prompt=system_prompt, user_prompt=user_prompt)


def create_client_from_env() -> OpenAICompatibleClient | None:
    config = load_model_config()
    if config.is_openai_compatible and config.configured:
        return OpenAICompatibleClient(config)
    return None


def doctor_check() -> dict[str, Any]:
    config = load_model_config()
    result: dict[str, Any] = {
        "provider": config.provider,
        "model_name": config.model_name,
        "base_url": config.base_url,
        "configured": config.configured,
    }
    if not config.is_openai_compatible:
        result["status"] = "mock"
        result["message"] = "Using mock provider; no remote model check needed."
        return result
    if not config.configured:
        result["status"] = "misconfigured"
        result["message"] = "OpenAI-compatible provider selected but OPENAI_BASE_URL / OPENAI_API_KEY / MODEL_NAME is incomplete."
        return result
    client = OpenAICompatibleClient(config)
    models = client.list_models()
    result["models"] = models
    if config.model_name not in models:
        result["status"] = "model_missing"
        result["message"] = f"Configured model '{config.model_name}' not found at /models."
        return result
    preview = client.chat(
        system_prompt="Reply with plain text only.",
        user_prompt="Reply with exactly: MODEL_OK",
        temperature=0.0,
    )
    result["status"] = "ok"
    result["message"] = preview
    return result
