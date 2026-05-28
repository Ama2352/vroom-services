package domain

import (
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
)

// --- Invalid transition tests ---

func TestTripInvalidTransitions(t *testing.T) {
	t.Run("Cannot start from REQUESTED", func(t *testing.T) {
		trip := &Trip{Status: StatusRequested}
		assert.ErrorIs(t, trip.Start(), ErrInvalidTripStatus)
	})

	t.Run("Cannot start from COMPLETED", func(t *testing.T) {
		trip := &Trip{Status: StatusCompleted}
		assert.ErrorIs(t, trip.Start(), ErrInvalidTripStatus)
	})

	t.Run("Cannot start from CANCELLED", func(t *testing.T) {
		trip := &Trip{Status: StatusCancelled}
		assert.ErrorIs(t, trip.Start(), ErrInvalidTripStatus)
	})

	t.Run("Cannot complete from REQUESTED", func(t *testing.T) {
		trip := &Trip{Status: StatusRequested}
		assert.ErrorIs(t, trip.Complete(25.0), ErrInvalidTripStatus)
	})

	t.Run("Cannot complete from ACCEPTED", func(t *testing.T) {
		trip := &Trip{Status: StatusAccepted}
		assert.ErrorIs(t, trip.Complete(25.0), ErrInvalidTripStatus)
	})

	t.Run("Cannot complete from CANCELLED", func(t *testing.T) {
		trip := &Trip{Status: StatusCancelled}
		assert.ErrorIs(t, trip.Complete(25.0), ErrInvalidTripStatus)
	})

	t.Run("Cannot accept from IN_PROGRESS", func(t *testing.T) {
		trip := &Trip{Status: StatusStarted}
		assert.ErrorIs(t, trip.AcceptByDriver(uuid.New()), ErrInvalidTripStatus)
	})

	t.Run("Cannot accept from COMPLETED", func(t *testing.T) {
		trip := &Trip{Status: StatusCompleted}
		assert.ErrorIs(t, trip.AcceptByDriver(uuid.New()), ErrInvalidTripStatus)
	})

	t.Run("Cannot accept from CANCELLED", func(t *testing.T) {
		trip := &Trip{Status: StatusCancelled}
		assert.ErrorIs(t, trip.AcceptByDriver(uuid.New()), ErrInvalidTripStatus)
	})

	t.Run("Cannot cancel IN_PROGRESS trip", func(t *testing.T) {
		trip := &Trip{Status: StatusStarted}
		assert.ErrorIs(t, trip.Cancel("mid-ride"), ErrInvalidTripStatus)
	})

	t.Run("Cannot cancel already-cancelled trip", func(t *testing.T) {
		trip := &Trip{Status: StatusCancelled}
		assert.ErrorIs(t, trip.Cancel("again"), ErrInvalidTripStatus)
	})

	t.Run("Cannot reject offer from COMPLETED", func(t *testing.T) {
		driverID := uuid.New()
		trip := &Trip{Status: StatusCompleted, DriverID: &driverID}
		assert.ErrorIs(t, trip.RejectOffer(driverID), ErrInvalidTripStatus)
	})

	t.Run("Cannot reject offer from CANCELLED", func(t *testing.T) {
		driverID := uuid.New()
		trip := &Trip{Status: StatusCancelled, DriverID: &driverID}
		assert.ErrorIs(t, trip.RejectOffer(driverID), ErrInvalidTripStatus)
	})

	t.Run("RejectOffer fails when no driver assigned", func(t *testing.T) {
		trip := &Trip{Status: StatusRequested}
		assert.Error(t, trip.RejectOffer(uuid.New()))
	})

	t.Run("RejectOffer fails for wrong driver", func(t *testing.T) {
		driverID := uuid.New()
		trip := &Trip{Status: StatusRequested, DriverID: &driverID}
		assert.Error(t, trip.RejectOffer(uuid.New()))
	})
}

// --- Side-effect / timestamp tests ---

func TestTripAcceptSetsTimestampAndDriver(t *testing.T) {
	before := time.Now().Add(-time.Millisecond)
	driverID := uuid.New()
	trip := &Trip{Status: StatusRequested}

	assert.NoError(t, trip.AcceptByDriver(driverID))

	assert.NotNil(t, trip.AcceptedAt)
	assert.True(t, trip.AcceptedAt.After(before))
	assert.Equal(t, driverID, *trip.DriverID)
	assert.Equal(t, StatusAccepted, trip.Status)
}

func TestTripRejectClearsDriver(t *testing.T) {
	driverID := uuid.New()
	trip := &Trip{Status: StatusRequested, DriverID: &driverID}

	assert.NoError(t, trip.RejectOffer(driverID))

	assert.Nil(t, trip.DriverID)
	assert.Equal(t, StatusRequested, trip.Status)
}

func TestTripCompleteSetsTimestampAndFinalPrice(t *testing.T) {
	before := time.Now().Add(-time.Millisecond)
	trip := &Trip{
		Status:         StatusStarted,
		EstimatedPrice: Price{Amount: 20.0, Currency: "VND"},
	}

	assert.NoError(t, trip.Complete(25.50))

	assert.Equal(t, StatusCompleted, trip.Status)
	assert.NotNil(t, trip.FinalPrice)
	assert.Equal(t, 25.50, trip.FinalPrice.Amount)
	assert.Equal(t, "VND", trip.FinalPrice.Currency)
	assert.NotNil(t, trip.CompletedAt)
	assert.True(t, trip.CompletedAt.After(before))
}

// --- Valid lifecycle tests (existing) ---

func TestTripLifecycle(t *testing.T) {
	tripID := uuid.New()
	passengerID := uuid.New()
	driverID := uuid.New()

	t.Run("Full valid lifecycle", func(t *testing.T) {
		trip := &Trip{
			ID:          tripID,
			PassengerID: passengerID,
			Status:      StatusRequested,
		}

		// 1. Accept
		err := trip.AcceptByDriver(driverID)
		assert.NoError(t, err)
		assert.Equal(t, StatusAccepted, trip.Status)
		assert.Equal(t, driverID, *trip.DriverID)

		// 2. Start
		err = trip.Start()
		assert.NoError(t, err)
		assert.Equal(t, StatusStarted, trip.Status)

		// 3. Complete
		err = trip.Complete(25.0)
		assert.NoError(t, err)
		assert.Equal(t, StatusCompleted, trip.Status)
		assert.Equal(t, 25.0, trip.FinalPrice.Amount)
	})

	t.Run("Allow assigned driver to accept", func(t *testing.T) {
		trip := &Trip{
			ID:          tripID,
			PassengerID: passengerID,
			Status:      StatusRequested,
			DriverID:    &driverID, // Tentatively assigned by Dispatch
		}

		// Should NOT return ErrDriverAlreadySet if it's the same driver
		err := trip.AcceptByDriver(driverID)
		assert.NoError(t, err)
	})

	t.Run("Reject different driver if already assigned", func(t *testing.T) {
		otherDriver := uuid.New()
		trip := &Trip{
			ID:          tripID,
			PassengerID: passengerID,
			Status:      StatusRequested,
			DriverID:    &driverID,
		}

		err := trip.AcceptByDriver(otherDriver)
		assert.ErrorIs(t, err, ErrDriverAlreadySet)
	})

	t.Run("Cancel trip", func(t *testing.T) {
		trip := &Trip{Status: StatusRequested}
		err := trip.Cancel("passenger changed mind")
		assert.NoError(t, err)
		assert.Equal(t, StatusCancelled, trip.Status)

		trip.Status = StatusCompleted
		err = trip.Cancel("too late")
		assert.ErrorIs(t, err, ErrInvalidTripStatus)
	})
}
