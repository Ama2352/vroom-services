package worker

import (
	"errors"
	"testing"
)

func TestUnsupportedContractIsPermanentFailure(t *testing.T) {
	result := classifyMessage("Trip.Requested.v2", nil)
	if result.disposition != dispositionPermanentFailure {
		t.Fatalf("unsupported contract disposition = %v, want permanent failure", result.disposition)
	}
}

func TestKnownHandlerDependencyErrorIsRetryable(t *testing.T) {
	result := classifyMessage("Trip.Requested", errors.New("redis timeout"))
	if result.disposition != dispositionRetryableFailure {
		t.Fatalf("known handler dependency error disposition = %v, want retryable failure", result.disposition)
	}
}

func TestInvalidKnownPayloadIsPermanentFailure(t *testing.T) {
	result := classifyMessage("Trip.Requested", permanentMessageError{cause: errors.New("invalid payload")})
	if result.disposition != dispositionPermanentFailure {
		t.Fatalf("invalid known payload disposition = %v, want permanent failure", result.disposition)
	}
}

func TestKnownHandlerSuccessIsTerminalSuccess(t *testing.T) {
	result := classifyMessage("Trip.Requested", nil)
	if result.disposition != dispositionSuccess {
		t.Fatalf("known handler success disposition = %v, want success", result.disposition)
	}
}
