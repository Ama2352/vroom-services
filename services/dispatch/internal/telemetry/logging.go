package telemetry

import (
	"context"
	"log/slog"

	"go.opentelemetry.io/otel/trace"
)

// TraceFields returns trace identifiers suitable for structured log fields.
// Invalid contexts intentionally produce no fields rather than fake IDs.
func TraceFields(ctx context.Context) map[string]string {
	sc := trace.SpanContextFromContext(ctx)
	if !sc.IsValid() {
		return map[string]string{}
	}
	return map[string]string{
		"trace_id": sc.TraceID().String(),
		"span_id":  sc.SpanID().String(),
	}
}

// LogError emits a diagnostic error while preserving correlation identifiers.
func LogError(ctx context.Context, logger *slog.Logger, operation string, err error, attrs ...any) {
	args := []any{"operation", operation}
	for key, value := range TraceFields(ctx) {
		args = append(args, key, value)
	}
	args = append(args, "error", err.Error())
	args = append(args, attrs...)
	logger.ErrorContext(ctx, err.Error(), args...)
}
