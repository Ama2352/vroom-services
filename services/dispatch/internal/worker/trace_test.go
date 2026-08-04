package worker

import (
	"context"
	"testing"

	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"
)

func TestExtractMessageContextUsesPublisherTrace(t *testing.T) {
	otel.SetTextMapPropagator(propagation.TraceContext{})
	parent := trace.NewSpanContext(trace.SpanContextConfig{
		TraceID:    trace.TraceID{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16},
		SpanID:     trace.SpanID{1, 2, 3, 4, 5, 6, 7, 8},
		TraceFlags: trace.FlagsSampled,
		Remote:     true,
	})
	carrier := propagation.MapCarrier{}
	otel.GetTextMapPropagator().Inject(trace.ContextWithSpanContext(context.Background(), parent), carrier)

	ctx := extractMessageContext(context.Background(), redis.XMessage{Values: map[string]any{
		"traceparent": carrier["traceparent"],
		"tracestate":  carrier["tracestate"],
	}})
	got := trace.SpanContextFromContext(ctx)
	if got.TraceID() != parent.TraceID() || got.SpanID() != parent.SpanID() {
		t.Fatalf("expected publisher trace %s/%s, got %s/%s", parent.TraceID(), parent.SpanID(), got.TraceID(), got.SpanID())
	}
}
