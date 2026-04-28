package domain

import (
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
)

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
