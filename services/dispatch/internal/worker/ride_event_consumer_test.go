package worker

import "testing"

func TestUnknownEventIsAckedOnlyAfterMaxRetries(t *testing.T) {
	if shouldAckEvent("Trip.Requested.v2", 1) {
		t.Fatal("unknown event was acknowledged before retry budget was exhausted")
	}
	if !shouldAckEvent("Trip.Requested.v2", maxEventRetries) {
		t.Fatal("unknown event was not acknowledged after retry budget was exhausted")
	}
}

func TestKnownEventIsAckedImmediately(t *testing.T) {
	if !shouldAckEvent("Trip.Requested", 1) {
		t.Fatal("known event was not acknowledged immediately")
	}
}
