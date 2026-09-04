import pytest

from agentic_interviewer.operations import (
    Correlation,
    InMemorySpanExporter,
    MetricRegistry,
    Tracer,
)


def test_metrics_reject_unbounded_or_pii_like_labels():
    metrics = MetricRegistry(allowed_labels={"capability", "outcome"})
    metrics.add("interviewer_requests_total", labels={"capability": "reasoning"})
    assert 'capability="reasoning"' in metrics.render_prometheus()
    with pytest.raises(ValueError):
        metrics.add("interviewer_requests_total", labels={"candidate_email": "a@example.com"})


def test_trace_keeps_correlation_but_drops_candidate_content():
    exporter = InMemorySpanExporter()
    tracer = Tracer(exporter)
    correlation = Correlation.from_ids(
        tenant_id="tenant-secret", interview_id="i-1", turn_id="t-1", salt="test"
    )
    with tracer.span(
        "model.call",
        correlation=correlation,
        attributes={"provider": "fake", "candidate.text": "My name is Jane Doe"},
    ):
        pass
    span = exporter.spans[0]
    assert span.status == "ok"
    assert span.attributes["provider"] == "fake"
    assert "candidate.text" not in span.attributes
    assert "tenant-secret" not in span.attributes.values()


def test_trace_drops_sensitive_content_even_when_value_looks_tag_safe():
    exporter = InMemorySpanExporter()
    tracer = Tracer(exporter)
    correlation = Correlation.from_ids(
        tenant_id="tenant", interview_id="i", turn_id="t", salt="test"
    )
    with tracer.span("model.call", correlation=correlation, attributes={"prompt": "secret-token"}):
        pass
    assert "prompt" not in exporter.spans[0].attributes


def test_trace_records_error_status_and_reraises():
    exporter = InMemorySpanExporter()
    tracer = Tracer(exporter)
    correlation = Correlation.from_ids(
        tenant_id="tenant", interview_id="i", turn_id="t", salt="test"
    )
    with pytest.raises(RuntimeError):
        with tracer.span("graph.node", correlation=correlation):
            raise RuntimeError("boom")
    assert exporter.spans[0].status == "error"
