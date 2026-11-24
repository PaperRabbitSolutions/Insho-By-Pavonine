from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Protocol
from uuid import UUID

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ai_execution import AIExecution
from app.models.project import Project
from app.models.workspace import Workspace
from app.schemas.ai import AIErrorResponse, AIExecutionRequest, AIExecutionResponse
from app.security import require_membership, require_project_access

logger = logging.getLogger("insho.ai")
_provider_slots = asyncio.Semaphore(10)


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    capabilities: frozenset[str]
    enabled: bool = True
    max_context_tokens: Optional[int] = None
    input_price_per_million: Optional[float] = None
    output_price_per_million: Optional[float] = None


@dataclass
class ProviderResult:
    output: str
    usage: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None


class AIProviderError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, status_code: int = 502, retry_after_seconds: Optional[float] = None):
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class ProviderAdapter(Protocol):
    async def execute(self, model: ModelConfig, request: AIExecutionRequest, timeout: float) -> ProviderResult: ...


class OpenAIResponsesAdapter:
    """OpenAI Responses API adapter; provider wire details stay behind this boundary."""

    async def execute(self, model: ModelConfig, request: AIExecutionRequest, timeout: float) -> ProviderResult:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise AIProviderError("configuration_failure", "The selected AI provider is not configured.", status_code=503)
        payload: dict[str, Any] = {"model": model.model, "input": request.input}
        if model.max_context_tokens is not None:
            input_bytes = len((request.input + (request.instructions or "")).encode("utf-8"))
            remaining = model.max_context_tokens - input_bytes
            payload["max_output_tokens"] = min(max(remaining, 1), int(os.getenv("AI_MAX_OUTPUT_TOKENS", "4096")))
        if request.instructions:
            payload["instructions"] = request.instructions
        if request.output_format == "json":
            if request.output_schema:
                payload["text"] = {"format": {"type": "json_schema", "name": "insho_output", "strict": False, "schema": request.output_schema}}
            else:
                payload["text"] = {"format": {"type": "json_object"}}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/responses",
                    headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise AIProviderError("provider_timeout", "The AI provider timed out.", retryable=True, status_code=504) from exc
        except httpx.RequestError as exc:
            raise AIProviderError("provider_unavailable", "The AI provider is temporarily unavailable.", retryable=True, status_code=502) from exc
        if response.is_error:
            status_code = response.status_code
            if status_code == 429:
                try:
                    body = response.json()
                    provider_code = body.get("error", {}).get("code")
                except (ValueError, AttributeError):
                    provider_code = None
                if provider_code in {"insufficient_quota", "billing_hard_limit_reached"}:
                    raise AIProviderError("quota_exceeded", "The AI provider quota is unavailable.", status_code=429)
                try:
                    retry_after = float(response.headers.get("Retry-After", ""))
                except ValueError:
                    retry_after = None
                raise AIProviderError("rate_limited", "The AI provider is rate limited.", retryable=True, status_code=429, retry_after_seconds=retry_after)
            if status_code in (401, 403):
                raise AIProviderError("authentication_failure", "The AI provider rejected its configured credentials.", status_code=502)
            if status_code == 404:
                raise AIProviderError("model_unavailable", "The configured AI model is unavailable.", status_code=503)
            if status_code in (408, 409) or status_code >= 500:
                raise AIProviderError("provider_unavailable", "The AI provider could not complete the request.", retryable=True, status_code=502)
            raise AIProviderError("invalid_request", "The AI provider rejected the request.", status_code=422)
        try:
            body = response.json()
            output = body.get("output_text")
            if not isinstance(output, str):
                texts = [part.get("text") for item in body.get("output", []) for part in item.get("content", []) if isinstance(part.get("text"), str)]
                output = "\n".join(texts)
            if not output:
                refused = [part.get("refusal") for item in body.get("output", []) for part in item.get("content", []) if isinstance(part.get("refusal"), str)]
                if refused:
                    raise AIProviderError("content_rejected", "The AI provider declined this request.", status_code=422)
            if not output:
                raise ValueError("empty output")
            usage_raw = body.get("usage")
            usage = None
            if isinstance(usage_raw, dict):
                usage = {
                    "input_tokens": usage_raw.get("input_tokens"),
                    "output_tokens": usage_raw.get("output_tokens"),
                    "total_tokens": usage_raw.get("total_tokens"),
                    "provider": usage_raw,
                }
            return ProviderResult(output=output, usage=usage, metadata={"response_id": body.get("id")})
        except (ValueError, TypeError, AttributeError) as exc:
            raise AIProviderError("invalid_output", "The AI provider returned an invalid response.", status_code=502) from exc


class AnthropicMessagesAdapter:
    """Anthropic Messages API adapter, enabled only with explicit model configuration."""

    async def execute(self, model: ModelConfig, request: AIExecutionRequest, timeout: float) -> ProviderResult:
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise AIProviderError("configuration_failure", "The selected AI provider is not configured.", status_code=503)
        try:
            max_tokens = int(os.getenv("AI_MAX_OUTPUT_TOKENS", "4096"))
        except ValueError as exc:
            raise AIProviderError("configuration_failure", "AI output token configuration is invalid.", status_code=503) from exc
        remaining_context = model.max_context_tokens - len((request.input + (request.instructions or "")).encode("utf-8")) if model.max_context_tokens is not None else max_tokens
        payload: dict[str, Any] = {
            "model": model.model,
            "max_tokens": max(1, min(max_tokens, remaining_context)),
            "messages": [{"role": "user", "content": request.input}],
        }
        if request.instructions:
            payload["system"] = request.instructions
        if request.output_format == "json":
            payload["output_config"] = {
                "format": {"type": "json_schema", "schema": request.output_schema or {"type": "object", "additionalProperties": True}}
            }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/") + "/v1/messages",
                    headers={
                        "Authorization": "Bearer " + api_key,
                        "anthropic-version": os.getenv("ANTHROPIC_API_VERSION", "2026-10-21"),
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise AIProviderError("provider_timeout", "The AI provider timed out.", retryable=True, status_code=504) from exc
        except httpx.RequestError as exc:
            raise AIProviderError("provider_unavailable", "The AI provider is temporarily unavailable.", retryable=True, status_code=502) from exc
        if response.is_error:
            if response.status_code == 429:
                try:
                    retry_after = float(response.headers.get("Retry-After", ""))
                except ValueError:
                    retry_after = None
                raise AIProviderError("rate_limited", "The AI provider is rate limited.", retryable=True, status_code=429, retry_after_seconds=retry_after)
            if response.status_code in (401, 403):
                raise AIProviderError("authentication_failure", "The AI provider rejected its configured credentials.", status_code=502)
            if response.status_code == 404:
                raise AIProviderError("model_unavailable", "The configured AI model is unavailable.", status_code=503)
            if response.status_code in (408, 409, 529) or response.status_code >= 500:
                raise AIProviderError("provider_unavailable", "The AI provider could not complete the request.", retryable=True, status_code=502)
            raise AIProviderError("invalid_request", "The AI provider rejected the request.", status_code=422)
        try:
            body = response.json()
            blocks = body.get("content", [])
            output = "\n".join(block["text"] for block in blocks if block.get("type") == "text" and isinstance(block.get("text"), str))
            if not output and any(block.get("type") == "refusal" for block in blocks):
                raise AIProviderError("content_rejected", "The AI provider declined this request.", status_code=422)
            if not output:
                raise ValueError("empty output")
            raw_usage = body.get("usage")
            usage = None
            if isinstance(raw_usage, dict):
                input_tokens, output_tokens = raw_usage.get("input_tokens"), raw_usage.get("output_tokens")
                usage = {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens if isinstance(input_tokens, int) and isinstance(output_tokens, int) else None,
                    "provider": raw_usage,
                }
            return ProviderResult(output=output, usage=usage, metadata={"response_id": body.get("id")})
        except (ValueError, TypeError, AttributeError, KeyError) as exc:
            raise AIProviderError("invalid_output", "The AI provider returned an invalid response.", status_code=502) from exc


class AIRouter:
    """Environment-backed, deterministic provider/model registry and capability router."""

    @staticmethod
    def models() -> list[ModelConfig]:
        providers = []
        enabled_values = {"1", "true", "yes", "on"}
        if os.getenv("AI_OPENAI_ENABLED", "true").strip().lower() in enabled_values and os.getenv("OPENAI_API_KEY", "").strip():
            providers.append(("openai", os.getenv("AI_OPENAI_MODELS", os.getenv("AI_OPENAI_MODEL", "gpt-4.1-mini")), os.getenv("AI_OPENAI_CAPABILITIES", "text")))
        if os.getenv("AI_ANTHROPIC_ENABLED", "false").strip().lower() in enabled_values and os.getenv("ANTHROPIC_API_KEY", "").strip():
            providers.append(("anthropic", os.getenv("AI_ANTHROPIC_MODELS", ""), os.getenv("AI_ANTHROPIC_CAPABILITIES", "text")))
        provider_order = [part.strip().lower() for part in os.getenv("AI_PROVIDER_ORDER", "openai,anthropic").split(",") if part.strip()]
        order_index = {provider: index for index, provider in enumerate(provider_order)}
        providers.sort(key=lambda item: order_index.get(item[0], len(order_index)))
        try:
            pricing = json.loads(os.getenv("AI_MODEL_PRICING_JSON", "{}"))
            contexts = json.loads(os.getenv("AI_MODEL_CONTEXT_LIMITS_JSON", "{}"))
            if not isinstance(pricing, dict) or not isinstance(contexts, dict):
                raise ValueError("configuration must be a JSON object")
            models = []
            for provider, raw_models, raw_capabilities in providers:
                capabilities = frozenset(part.strip().lower() for part in raw_capabilities.split(",") if part.strip())
                model_ids = [part.strip() for part in raw_models.split(",") if part.strip()]
                for model_id in model_ids:
                    key = f"{provider}:{model_id}"
                    prices = pricing.get(key, pricing.get(model_id, {}))
                    if not isinstance(prices, dict):
                        raise ValueError("model pricing must be an object")
                    input_price = float(prices["input_per_million"]) if prices.get("input_per_million") is not None else None
                    output_price = float(prices["output_per_million"]) if prices.get("output_per_million") is not None else None
                    if (input_price is None) != (output_price is None):
                        raise ValueError("both input and output prices must be configured together")
                    context_limit = int(contexts.get(key, contexts.get(model_id))) if contexts.get(key, contexts.get(model_id)) is not None else None
                    if any(value is not None and value < 0 for value in (input_price, output_price)) or (context_limit is not None and context_limit <= 0):
                        raise ValueError("prices and context limits must be positive")
                    models.append(ModelConfig(provider, model_id, capabilities, max_context_tokens=context_limit,
                                              input_price_per_million=input_price, output_price_per_million=output_price))
            return models
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise AIProviderError("configuration_failure", "AI model metadata configuration is invalid.", status_code=503) from exc

    @classmethod
    def route(cls, request: AIExecutionRequest, *, excluded: set[str] | None = None) -> ModelConfig:
        excluded = excluded or set()
        models = cls.models()
        if request.preferred_provider:
            models = [model for model in models if model.provider == request.preferred_provider]
        if request.preferred_model:
            models = [model for model in models if model.model == request.preferred_model]
        needed = set(request.required_capabilities)
        if request.output_format == "json":
            needed.add("structured_output")
        input_bytes = len((request.input + (request.instructions or "")).encode("utf-8"))
        candidates = [model for model in models if model.enabled and f"{model.provider}:{model.model}" not in excluded
                      and needed.issubset(model.capabilities)
                      and (model.max_context_tokens is None or input_bytes < model.max_context_tokens)]
        if not candidates:
            if not models and not os.getenv("OPENAI_API_KEY") and not os.getenv("ANTHROPIC_API_KEY"):
                raise AIProviderError("configuration_failure", "No AI provider is configured for execution.", status_code=503)
            raise AIProviderError("capability_unsupported", "No enabled model supports the requested capabilities.", status_code=422)
        return candidates[0]


def _cost(model: ModelConfig, usage: Optional[dict[str, Any]]) -> Optional[float]:
    if not usage or model.input_price_per_million is None or model.output_price_per_million is None:
        return None
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return None
    return (input_tokens * model.input_price_per_million + output_tokens * model.output_price_per_million) / 1_000_000


def _validate_output(output: str, output_format: str) -> None:
    if len(output) > 200_000:
        raise AIProviderError("invalid_output", "The AI response exceeded the allowed size.", status_code=502)
    if output_format == "json":
        try:
            json.loads(output)
        except (ValueError, TypeError) as exc:
            raise AIProviderError("invalid_output", "The AI provider returned malformed JSON.", status_code=502) from exc


class AIExecutionService:
    def __init__(self, adapters: Optional[dict[str, ProviderAdapter]] = None, *, sleep=asyncio.sleep):
        self.adapters = adapters or {"openai": OpenAIResponsesAdapter(), "anthropic": AnthropicMessagesAdapter()}
        self.sleep = sleep

    async def execute(self, db: Session, request: AIExecutionRequest, user_id: UUID, *,
                      provenance: Optional[dict] = None) -> AIExecutionResponse:
        organization_id: UUID
        workspace_id = request.workspace_id
        if request.project_id:
            project = require_project_access(db, user_id, request.project_id)
            organization_id = project.organization_id
            if workspace_id and workspace_id != project.workspace_id:
                raise HTTPException(status_code=404, detail="Resource not found")
            workspace_id = project.workspace_id
        elif workspace_id:
            workspace = db.get(Workspace, workspace_id)
            if not workspace:
                raise HTTPException(status_code=404, detail="Resource not found")
            require_membership(db, user_id, workspace.organization_id)
            organization_id = workspace.organization_id
        else:
            raise HTTPException(status_code=422, detail="project_id or workspace_id is required")

        if request.idempotency_key:
            request_fingerprint = hashlib.sha256(json.dumps({
                "task": request.task, "input": request.input, "instructions": request.instructions,
                "format": request.output_format, "provider": request.preferred_provider,
                "model": request.preferred_model, "project_id": str(request.project_id or ""),
                "output_schema": request.output_schema,
            }, sort_keys=True).encode()).hexdigest()
            previous = db.scalars(select(AIExecution).where(
                AIExecution.user_id == user_id,
                AIExecution.organization_id == organization_id,
                AIExecution.idempotency_key == request.idempotency_key,
            )).first()
            if previous:
                if (previous.metadata_json or {}).get("request_fingerprint") != request_fingerprint:
                    raise HTTPException(status_code=409, detail="Idempotency key was already used for a different request")
                if previous.status == "completed":
                    return self._response(previous)
                raise HTTPException(status_code=409, detail="An execution with this idempotency key is already in progress or failed")

        try:
            retries = min(max(int(os.getenv("AI_MAX_RETRIES", "2")), 0), 4)
            timeout = float(request.timeout_seconds or os.getenv("AI_TIMEOUT_SECONDS", "45"))
            timeout = min(max(timeout, 1), 180)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail="AI execution configuration is invalid") from exc

        metadata = {"client_metadata": request.metadata,
                    "request_fingerprint": request_fingerprint if request.idempotency_key else None}
        if provenance is not None:
            metadata["provenance"] = provenance
        record = AIExecution(
            request_id=request.request_id, user_id=user_id, organization_id=organization_id,
            workspace_id=workspace_id, project_id=request.project_id, idempotency_key=request.idempotency_key,
            task=request.task, feature=request.feature, output_format=request.output_format,
            metadata_json=metadata, status="running",
        )
        db.add(record)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if request.idempotency_key:
                previous = db.scalars(select(AIExecution).where(
                    AIExecution.user_id == user_id,
                    AIExecution.organization_id == organization_id,
                    AIExecution.idempotency_key == request.idempotency_key,
                )).first()
                if previous:
                    if (previous.metadata_json or {}).get("request_fingerprint") != request_fingerprint:
                        raise HTTPException(status_code=409, detail="Idempotency key was already used for a different request") from exc
                    if previous.status == "completed":
                        return self._response(previous)
                    raise HTTPException(status_code=409, detail="An execution with this idempotency key is already in progress or failed") from exc
            raise HTTPException(status_code=409, detail="AI execution could not be recorded") from exc
        db.refresh(record)
        started = time.monotonic()
        attempted: set[str] = set()
        fallback_from: Optional[str] = None
        last_error: Optional[AIProviderError] = None
        try:
            # Primary and one distinct compatible fallback; no provider/model is attempted twice.
            for candidate_number in range(2):
                try:
                    model = AIRouter.route(request, excluded=attempted)
                except AIProviderError as route_error:
                    if candidate_number == 0:
                        raise
                    last_error = last_error or route_error
                    break
                model_key = f"{model.provider}:{model.model}"
                if model_key in attempted:
                    break
                if candidate_number:
                    fallback_from = fallback_from or f"{record.provider}:{record.model}"
                attempted.add(model_key)
                record.provider, record.model = model.provider, model.model
                record.metadata_json = {**(record.metadata_json or {}), "routing": {"selected": model_key, "attempt": candidate_number + 1}}
                db.commit()
                adapter = self.adapters.get(model.provider)
                if not adapter:
                    last_error = AIProviderError("configuration_failure", "The selected provider adapter is unavailable.", status_code=503)
                else:
                    for attempt in range(retries + 1):
                        try:
                            logger.info("AI provider call started", extra={"execution_id": str(record.id), "request_id": str(record.request_id), "provider": model.provider, "model": model.model, "attempt": attempt + 1})
                            async with _provider_slots:
                                result = await adapter.execute(model, request, timeout)
                            _validate_output(result.output, request.output_format)
                            record.output = result.output
                            record.usage = result.usage
                            record.input_tokens = (result.usage or {}).get("input_tokens")
                            record.output_tokens = (result.usage or {}).get("output_tokens")
                            record.estimated_cost_usd = _cost(model, result.usage)
                            record.metadata_json = {**(record.metadata_json or {}), "provider_metadata": result.metadata or {}}
                            if model.input_price_per_million is not None and model.output_price_per_million is not None:
                                record.metadata_json["pricing_snapshot"] = {
                                    "currency": "USD", "basis": "configured_per_million_tokens",
                                    "input_per_million": model.input_price_per_million,
                                    "output_per_million": model.output_price_per_million,
                                }
                            record.fallback_from = fallback_from
                            record.status = "completed"
                            record.completed_at = datetime.now(timezone.utc)
                            record.duration_ms = int((time.monotonic() - started) * 1000)
                            db.commit()
                            logger.info("AI execution completed", extra={"execution_id": str(record.id), "provider": model.provider, "model": model.model, "duration_ms": int((time.monotonic()-started)*1000)})
                            return self._response(record)
                        except AIProviderError as exc:
                            last_error = exc
                            if not exc.retryable or attempt >= retries:
                                break
                            record.retry_count = (record.retry_count or 0) + 1
                            delay = exc.retry_after_seconds if exc.retry_after_seconds is not None else 0.25 * (2 ** attempt) + random.random() * 0.1
                            await self.sleep(min(max(delay, 0), 30))
                if not last_error or not last_error.retryable:
                    break
            raise last_error or AIProviderError("execution_failure", "AI execution failed.")
        except AIProviderError as exc:
            record.status = "failed"
            record.error_code = exc.code
            record.fallback_from = fallback_from
            record.completed_at = datetime.now(timezone.utc)
            record.duration_ms = int((time.monotonic() - started) * 1000)
            db.commit()
            logger.warning("AI execution failed", extra={"execution_id": str(record.id), "request_id": str(record.request_id), "error_code": exc.code})
            raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.safe_message, "retryable": exc.retryable, "execution_id": str(record.id), "request_id": str(record.request_id)}) from exc
        except asyncio.CancelledError:
            record.status = "cancelled"
            record.error_code = "cancelled"
            record.completed_at = datetime.now(timezone.utc)
            record.duration_ms = int((time.monotonic() - started) * 1000)
            db.commit()
            raise
        except Exception as exc:
            # Keep adapter/serialization/database-independent failures safe and terminal.
            logger.error("Unexpected AI execution failure", extra={"execution_id": str(record.id), "request_id": str(record.request_id)})
            record.status = "failed"
            record.error_code = "execution_failure"
            record.completed_at = datetime.now(timezone.utc)
            record.duration_ms = int((time.monotonic() - started) * 1000)
            db.commit()
            raise HTTPException(status_code=502, detail={"code": "execution_failure", "message": "AI execution failed.", "retryable": False, "execution_id": str(record.id), "request_id": str(record.request_id)}) from exc
        finally:
            if record.status in {"completed", "failed"}:
                record.completed_at = record.completed_at or datetime.now(timezone.utc)
                record.duration_ms = record.duration_ms or int((time.monotonic() - started) * 1000)
                db.commit()

    @staticmethod
    def _response(record: AIExecution) -> AIExecutionResponse:
        safe_metadata = {key: value for key, value in (record.metadata_json or {}).items() if key != "request_fingerprint"}
        error_messages = {
            "provider_timeout": "The AI provider timed out.",
            "provider_unavailable": "The AI provider is temporarily unavailable.",
            "rate_limited": "The AI provider is rate limited.",
            "authentication_failure": "The AI provider rejected its configured credentials.",
            "invalid_request": "The AI provider rejected the request.",
            "model_unavailable": "The configured AI model is unavailable.",
            "capability_unsupported": "No enabled model supports the requested capabilities.",
            "invalid_output": "The AI provider returned an invalid response.",
            "configuration_failure": "The selected AI provider is not configured.",
            "content_rejected": "The AI provider declined this request.",
            "quota_exceeded": "The AI provider quota is unavailable.",
            "execution_failure": "AI execution failed.",
            "cancelled": "AI execution was cancelled.",
        }
        error = None
        if record.status == "failed" and record.error_code:
            code = record.error_code
            error = AIErrorResponse(code=code, message=error_messages.get(code, "AI execution failed."), retryable=code in {"provider_timeout", "provider_unavailable", "rate_limited"})
        return AIExecutionResponse(
            execution_id=record.id, request_id=record.request_id, task=record.task,
            success=record.status == "completed", output=record.output, output_format=record.output_format,
            provider=record.provider, model=record.model, duration_ms=record.duration_ms,
            usage=record.usage, estimated_cost_usd=float(record.estimated_cost_usd) if record.estimated_cost_usd is not None else None,
            retry_count=record.retry_count or 0, fallback_from=record.fallback_from,
            metadata=safe_metadata,
            error=error,
        )


# The API and feature modules share this central orchestrator instance.
ai_execution_service = AIExecutionService()
