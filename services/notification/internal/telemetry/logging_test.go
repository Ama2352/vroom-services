package telemetry

import (
	"context"
	"testing"

	"go.opentelemetry.io/otel/trace"
)

func TestTraceFieldsIncludesValidSpanContext(t *testing.T) {
	sc := trace.NewSpanContext(trace.SpanContextConfig{
		TraceID: trace.TraceID{0x4b, 0xf9, 0x2f, 0x35, 0x77, 0xb3, 0x4d, 0xa6, 0xa3, 0xce, 0x92, 0x9d, 0x0e, 0x0e, 0x47, 0x36},
		SpanID:  trace.SpanID{0x00, 0xf0, 0x67, 0xaa, 0x0b, 0xa9, 0x02, 0xb7},
	})
	fields := TraceFields(trace.ContextWithSpanContext(context.Background(), sc))
	if fields["trace_id"] != sc.TraceID().String() || fields["span_id"] != sc.SpanID().String() {
		t.Fatalf("unexpected trace fields: %#v", fields)
	}
}

func TestTraceFieldsOmitsInvalidContext(t *testing.T) {
	if fields := TraceFields(context.Background()); len(fields) != 0 {
		t.Fatalf("expected no trace fields, got %#v", fields)
	}
}
