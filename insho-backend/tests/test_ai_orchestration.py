import asyncio

import pytest

from app.api.v1.ai import execution_service
from app.models.project import Project
from app.schemas.ai import AIExecutionRequest
from app.services import ai_orchestration
from app.services.ai_orchestration import (
    AIRouter, AIProviderError, AnthropicMessagesAdapter, ModelConfig,
    OpenAIResponsesAdapter, ProviderResult, _cost, _validate_output,
)


def test_router_requires_declared_capabilities(monkeypatch):
    monkeypatch.setenv("AI_OPENAI_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AI_OPENAI_MODELS", "primary,backup")
    monkeypatch.setenv("AI_OPENAI_CAPABILITIES", "text")
    request = AIExecutionRequest(task="summarize", input="brief", feature="test", required_capabilities=["text", "vision"])
    with pytest.raises(AIProviderError) as error:
        AIRouter.route(request)
    assert error.value.code == "capability_unsupported"


def test_router_uses_deterministic_model_order(monkeypatch):
    monkeypatch.setenv("AI_OPENAI_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AI_OPENAI_MODELS", "first,second")
    monkeypatch.setenv("AI_OPENAI_CAPABILITIES", "text,structured_output")
    request = AIExecutionRequest(task="summarize", input="brief", feature="test", output_format="json")
    assert AIRouter.route(request).model == "first"
    assert AIRouter.route(request, excluded={"openai:first"}).model == "second"


def test_cost_is_only_estimated_with_configured_prices_and_reported_usage():
    configured = ModelConfig("openai", "known", frozenset({"text"}), input_price_per_million=2.0, output_price_per_million=8.0)
    unknown = ModelConfig("openai", "unknown", frozenset({"text"}))
    assert _cost(configured, {"input_tokens": 1000, "output_tokens": 500}) == pytest.approx(0.006)
    assert _cost(unknown, {"input_tokens": 1000, "output_tokens": 500}) is None
    assert _cost(configured, None) is None


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.is_error = status_code >= 400

    def json(self):
        return self.payload


def install_fake_http(monkeypatch, response, captured):
    class FakeAsyncClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, headers, json):
            captured.update(url=url, headers=headers, payload=json)
            return response

    monkeypatch.setattr(ai_orchestration.httpx, "AsyncClient", FakeAsyncClient)


def test_openai_adapter_normalizes_responses_api_payload(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    captured = {}
    install_fake_http(monkeypatch, FakeResponse({
        "id": "resp_test", "output_text": '{"ok":true}',
        "usage": {"input_tokens": 11, "output_tokens": 4, "total_tokens": 15},
    }), captured)
    request = AIExecutionRequest(task="extract", input="brief", instructions="Return JSON", feature="test", output_format="json")
    result = asyncio.run(OpenAIResponsesAdapter().execute(ModelConfig("openai", "configured", frozenset({"text", "structured_output"})), request, 12))
    assert captured["url"].endswith("/responses")
    assert captured["payload"]["text"]["format"]["type"] == "json_object"
    assert result.output == '{"ok":true}'
    assert result.usage["total_tokens"] == 15
    assert result.metadata["response_id"] == "resp_test"


def test_anthropic_adapter_uses_messages_api_and_normalizes_usage(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    monkeypatch.setenv("ANTHROPIC_API_VERSION", "2026-10-21")
    captured = {}
    install_fake_http(monkeypatch, FakeResponse({
        "id": "msg_test", "content": [{"type": "text", "text": "ready"}],
        "usage": {"input_tokens": 7, "output_tokens": 2},
    }), captured)
    request = AIExecutionRequest(task="summarize", input="brief", feature="test", instructions="Be concise")
    result = asyncio.run(AnthropicMessagesAdapter().execute(ModelConfig("anthropic", "configured", frozenset({"text"})), request, 9))
    assert captured["url"].endswith("/v1/messages")
    assert captured["headers"]["anthropic-version"] == "2026-10-21"
    assert captured["headers"]["Authorization"] == "Bearer test-secret"
    assert captured["payload"]["system"] == "Be concise"
    assert result.output == "ready"
    assert result.usage["total_tokens"] == 9


def test_provider_adapter_rate_limit_is_normalized(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    captured = {}
    install_fake_http(monkeypatch, FakeResponse({"error": {"code": "rate_limit"}}, 429, {"Retry-After": "3"}), captured)
    request = AIExecutionRequest(task="summarize", input="brief", feature="test")
    with pytest.raises(AIProviderError) as error:
        asyncio.run(OpenAIResponsesAdapter().execute(ModelConfig("openai", "configured", frozenset({"text"})), request, 5))
    assert error.value.code == "rate_limited"
    assert error.value.retryable is True
    assert error.value.retry_after_seconds == 3


def test_missing_provider_credentials_and_invalid_output_fail_safely(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    request = AIExecutionRequest(task="summarize", input="brief", feature="test")
    with pytest.raises(AIProviderError) as error:
        asyncio.run(AnthropicMessagesAdapter().execute(ModelConfig("anthropic", "configured", frozenset({"text"})), request, 5))
    assert error.value.code == "configuration_failure"
    with pytest.raises(AIProviderError) as invalid:
        _validate_output("not-json", "json")
    assert invalid.value.code == "invalid_output"


class FakeAdapter:
    async def execute(self, model, request, timeout):
        return ProviderResult(output='{"summary":"ok"}', usage={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13})


class RetryThenSuccessAdapter:
    def __init__(self):
        self.calls = 0

    async def execute(self, model, request, timeout):
        self.calls += 1
        if self.calls == 1:
            raise AIProviderError("provider_unavailable", "temporary", retryable=True)
        return ProviderResult(output="done")


class PrimaryFailsAdapter:
    async def execute(self, model, request, timeout):
        if model.model == "primary":
            raise AIProviderError("provider_unavailable", "temporary", retryable=True)
        return ProviderResult(output="fallback result")


class FallbackSuccessAdapter:
    async def execute(self, model, request, timeout):
        return ProviderResult(output="fallback result")


def test_api_feature_call_routes_through_execution_service(client, db_session, identity_factory, monkeypatch):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = Project(
        organization_id=identity["organization"].id,
        workspace_id=identity["workspace"].id,
        name="AI test project",
        status="draft",
    )
    db_session.add(project)
    db_session.commit()
    monkeypatch.setenv("AI_OPENAI_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AI_OPENAI_MODELS", "fake-model")
    monkeypatch.setenv("AI_OPENAI_CAPABILITIES", "text,structured_output")
    monkeypatch.setattr(execution_service, "adapters", {"openai": FakeAdapter()})
    response = client.post("/api/v1/ai/execute", json={
        "task": "summarize", "input": "event brief", "feature": "briefs",
        "project_id": str(project.id), "output_format": "json", "idempotency_key": "request-1",
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    assert payload["provider"] == "openai"
    assert payload["model"] == "fake-model"
    assert payload["usage"]["input_tokens"] == 10
    assert payload["duration_ms"] is not None
    replay = client.post("/api/v1/ai/execute", json={
        "task": "summarize", "input": "event brief", "feature": "briefs",
        "project_id": str(project.id), "output_format": "json", "idempotency_key": "request-1",
    })
    assert replay.status_code == 200
    assert replay.json()["execution_id"] == payload["execution_id"]
    history = client.get(f"/api/v1/ai/projects/{project.id}/executions")
    assert history.status_code == 200
    assert len(history.json()) == 1
    summary = client.post(f"/api/v1/context/projects/{project.id}/summarize")
    assert summary.status_code == 200, summary.text
    assert summary.json()["success"] is True
    assert summary.json()["metadata"]["client_metadata"] == {}
    history = client.get(f"/api/v1/ai/projects/{project.id}/executions")
    assert len(history.json()) == 2


def test_ai_history_is_project_isolated(client, db_session, identity_factory):
    other = identity_factory()
    project = Project(
        organization_id=other["organization"].id,
        workspace_id=other["workspace"].id,
        name="Private project",
        status="draft",
    )
    db_session.add(project)
    db_session.commit()
    response = client.get(f"/api/v1/ai/projects/{project.id}/executions")
    assert response.status_code == 404


def test_ai_execute_rejects_project_from_another_organization(client, db_session, identity_factory):
    other = identity_factory()
    project = Project(organization_id=other["organization"].id, workspace_id=other["workspace"].id, name="Private project", status="draft")
    db_session.add(project)
    db_session.commit()
    response = client.post("/api/v1/ai/execute", json={
        "task": "summarize", "input": "private brief", "feature": "test", "project_id": str(project.id),
    })
    assert response.status_code == 404


@pytest.mark.parametrize("fallback", [False, True])
def test_retry_and_compatible_fallback(client, db_session, identity_factory, monkeypatch, fallback):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = Project(organization_id=identity["organization"].id, workspace_id=identity["workspace"].id, name="Retry project", status="draft")
    db_session.add(project)
    db_session.commit()
    monkeypatch.setenv("AI_OPENAI_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AI_OPENAI_MODELS", "primary")
    monkeypatch.setenv("AI_OPENAI_CAPABILITIES", "text")
    monkeypatch.setenv("AI_MAX_RETRIES", "0" if fallback else "1")
    if fallback:
        monkeypatch.setenv("AI_ANTHROPIC_ENABLED", "true")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("AI_ANTHROPIC_MODELS", "secondary")
        monkeypatch.setenv("AI_ANTHROPIC_CAPABILITIES", "text")
        monkeypatch.setattr(execution_service, "adapters", {"openai": PrimaryFailsAdapter(), "anthropic": FallbackSuccessAdapter()})
    else:
        adapter = RetryThenSuccessAdapter()
        monkeypatch.setattr(execution_service, "adapters", {"openai": adapter})
        async def no_wait(_seconds):
            return None
        monkeypatch.setattr(execution_service, "sleep", no_wait)
    response = client.post("/api/v1/ai/execute", json={
        "task": "summarize", "input": "brief", "feature": "test", "project_id": str(project.id),
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["success"] is True
    if fallback:
        assert result["provider"] == "anthropic"
        assert result["model"] == "secondary"
        assert result["fallback_from"] == "openai:primary"
    else:
        assert result["retry_count"] == 1
