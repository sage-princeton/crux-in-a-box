from crux_scaffold.telemetry import NullTelemetry, RunIdentity, telemetry_from_env

ENV = {"RUN_SLUG": "Demo-Slug", "CRUX_WORKSPACE_ID": "ws1", "CRUX_MODEL": "gpt-test", "CRUX_REASONING_EFFORT": "low"}


def test_identity_matches_the_acp_platforms_and_lowercases_the_environment():
    identity = RunIdentity.from_env(ENV)
    assert identity.metadata() == {"workspaceId": "ws1", "runSlug": "Demo-Slug", "agentPlatform": "openai-agents",
                                   "configuredModel": "gpt-test", "configuredEffort": "low"}
    assert identity.tags() == ["workspace:ws1", "run:Demo-Slug", "platform:openai-agents"]
    assert identity.environment == "demo-slug"
    assert RunIdentity.from_env({}).run_slug == "local"


def test_without_langfuse_keys_telemetry_is_off():
    assert isinstance(telemetry_from_env({"LANGFUSE_PUBLIC_KEY": "pk-only"}, RunIdentity.from_env({})), NullTelemetry)
