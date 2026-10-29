"""Read SDK provisioning requirements from scaffold.toml using only the standard library."""

import argparse
import json
import re
import sys
import tomllib
from urllib.parse import urlsplit


class ConfigurationError(Exception):
    """An operator-facing error containing no unvalidated configuration values."""


def requirements(config: dict, model_provider: str, model: str) -> dict:
    runtime = config.get("runtime", {})
    if runtime.get("type", "openai-agents") != "openai-agents":
        raise ConfigurationError("EC2 SDK provisioning requires runtime.type=openai-agents")
    provider = runtime.get("model_provider")
    key, base_url, api, extra_body = "OPENAI_API_KEY", None, "responses", {}
    if provider is not None:
        if not isinstance(provider, dict) or provider.keys() - {
            "type", "api", "base_url", "api_key_env", "extra_body"
        }:
            raise ConfigurationError("Invalid runtime.model_provider fields")
        kind, api = provider.get("type"), provider.get("api")
        if kind not in {"openai", "openai-compatible"} or api not in {"responses", "chat-completions"}:
            raise ConfigurationError("runtime.model_provider requires valid type and api")
        key = provider.get("api_key_env", key)
        if not isinstance(key, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) or key in {
            "PATH", "HOME", "PYTHONPATH", "XDG_CACHE_HOME", "CODEX_HOME", "CLAUDE_CODE_EXECUTABLE"
        } or key.startswith(("CRUX_", "LANGFUSE_", "AGENTRQ_", "AWS_")):
            raise ConfigurationError("Invalid or reserved runtime.model_provider.api_key_env")
        base_url, extra_body = provider.get("base_url"), provider.get("extra_body", {})
        if not isinstance(extra_body, dict):
            raise ConfigurationError("runtime.model_provider.extra_body must be a table")
        if kind == "openai":
            if base_url or extra_body or api != "responses" or key != "OPENAI_API_KEY":
                raise ConfigurationError("Custom provider settings require type=openai-compatible")
        else:
            if not isinstance(base_url, str):
                raise ConfigurationError("Compatible providers require an HTTPS base_url")
            url = urlsplit(base_url)
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ConfigurationError("Provider base_url must be HTTPS without credentials, query or fragment")
    openrouter = base_url is not None and urlsplit(base_url).hostname == "openrouter.ai"
    if base_url and config.get("context", {}).get("type") == "openai_compaction":
        raise ConfigurationError("Compatible providers require persistent or trim_recent context; openai_compaction calls direct OpenAI")
    if model_provider == "openrouter" and not openrouter:
        raise ConfigurationError("MODEL_PROVIDER=openrouter requires an OpenRouter runtime.model_provider in the committed drop-in")
    coding = config.get("coding_agents", {})
    if not isinstance(coding, dict) or any(spec.get("type", name) != "codex" for name, spec in coding.items()):
        raise ConfigurationError("EC2 SDK provisioning currently supports codex coding agents only")
    result = {"api_key_env": key, "coding_agent": "codex" if coding else None}
    if coding and base_url:
        if api != "responses":
            raise ConfigurationError("Codex coding agents require a Responses provider")
        if extra_body and (not openrouter or not re.search(r"@preset/[A-Za-z0-9_-]+$", model)):
            raise ConfigurationError("Codex cannot forward runtime.model_provider.extra_body; for OpenRouter use model@preset/NAME with matching routing settings")
        result.update(codex_base_url=base_url, codex_api_key_env=key)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-provider", choices=("direct", "openrouter"), default="direct")
    parser.add_argument("--model", default="")
    args = parser.parse_args()
    try:
        config = tomllib.loads(sys.stdin.read())
        result = requirements(config, args.model_provider, args.model)
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (ValueError, TypeError, AttributeError):
        # TOML/parser errors may include source text; never echo unvalidated values.
        print("Invalid SDK provider configuration; check provider fields, HTTPS endpoint, credential name, "
              "and Codex Responses/preset requirements. MODEL_PROVIDER=openrouter requires an OpenRouter drop-in.",
              file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
