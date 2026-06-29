"""OpenTelemetry integration for SkillsOps.

Graceful degradation: if the OpenTelemetry packages are not installed,
``configure_telemetry`` returns a no-op tracer and all spans become no-ops.

Install with: ``pip install 'skillsops[observability]'``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional


def configure_telemetry(
    service_name: str = "skillsops",
    endpoint: Optional[str] = None,
    exporter: str = "otlp",
    resource_attributes: Optional[dict] = None,
) -> "SkillsOpsTracer":
    """Configure OpenTelemetry. Returns a no-op tracer if OTel is unavailable.

    exporter: "otlp" (production), "console" (debug), or "none".
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

        resource = Resource.create({"service.name": service_name, **(resource_attributes or {})})
        provider = TracerProvider(resource=resource)

        if exporter == "console":
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        elif exporter == "otlp":
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))

        trace.set_tracer_provider(provider)
        tracer = trace.get_tracer("skillsops", "0.2.0")
        return SkillsOpsTracer(tracer=tracer, enabled=True, provider=provider)
    except ImportError:
        return SkillsOpsTracer(tracer=None, enabled=False)


def tracer_from_provider(provider) -> "SkillsOpsTracer":
    """Build a tracer from an existing OTel TracerProvider (used in tests)."""
    tracer = provider.get_tracer("skillsops", "0.2.0")
    return SkillsOpsTracer(tracer=tracer, enabled=True, provider=provider)


class SkillsOpsTracer:
    """Wrapper around an OTel tracer with SkillsOps convenience methods."""

    def __init__(self, tracer=None, enabled: bool = False, provider=None):
        self._tracer = tracer
        self._enabled = enabled
        self._provider = provider

    @property
    def enabled(self) -> bool:
        return self._enabled

    @asynccontextmanager
    async def skill_invocation_span(self, context):
        if not self._enabled:
            yield NoOpSpan()
            return
        with self._tracer.start_as_current_span(
            "skill.invoke",
            attributes={
                "skill.name": context.skill_name,
                "skill.version": context.skill_version,
                "skill.namespace": context.skill_namespace,
                "skill.category": context.skill_category,
                "actor.id": context.actor_id,
                "actor.roles": ",".join(context.actor_roles),
                "environment": context.environment,
                "invocation.id": context.invocation_id,
            },
        ) as span:
            yield OTelSpan(span)

    @asynccontextmanager
    async def registry_operation_span(self, operation: str, **attributes):
        if not self._enabled:
            yield NoOpSpan()
            return
        with self._tracer.start_as_current_span(f"registry.{operation}", attributes=attributes) as span:
            yield OTelSpan(span)


class OTelSpan:
    """Wrapper around an OTel span with convenience methods."""

    def __init__(self, span):
        self._span = span

    def add_policy_event(self, phase: str, hook_name: str, decision: str, reason: str):
        self._span.add_event(
            f"policy.{phase}.evaluated",
            attributes={"hook.name": hook_name, "decision": decision, "reason": reason},
        )

    def set_error(self, error: Exception):
        from opentelemetry.trace import Status, StatusCode

        self._span.set_status(Status(StatusCode.ERROR, str(error)))
        self._span.record_exception(error)

    def set_result(self, success: bool, duration_ms: float):
        self._span.set_attribute("execution.success", success)
        self._span.set_attribute("execution.duration_ms", duration_ms)


class NoOpSpan:
    """No-op span used when OTel is unavailable."""

    def add_policy_event(self, *args, **kwargs):
        pass

    def set_error(self, *args, **kwargs):
        pass

    def set_result(self, *args, **kwargs):
        pass
