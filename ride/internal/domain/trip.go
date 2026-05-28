package domain

import (
	"errors"
	"time"

	"github.com/google/uuid"
)

var (
	ErrInvalidTripStatus = errors.New("invalid trip status for this action")
	ErrDriverAlreadySet  = errors.New("driver already assigned to this trip")
)

type TripStatus string

const (
	StatusRequested TripStatus = "REQUESTED"
	StatusAccepted  TripStatus = "ACCEPTED"
	StatusStarted   TripStatus = "IN_PROGRESS"
	StatusCompleted TripStatus = "COMPLETED"
	StatusCancelled TripStatus = "CANCELLED"
)

type Price struct {
	Amount   float64 `json:"amount"`
	Currency string  `json:"currency"`
}

type GeoPoint struct {
	Lat float64 `json:"lat"`
	Lng float64 `json:"lng"`
}

type Location struct {
	Point   GeoPoint `json:"point"`
	Address string   `json:"address,omitempty"`
}

type Trip struct {
	ID             uuid.UUID  `json:"id"`
	PassengerID    uuid.UUID  `json:"passenger_id"`
	DriverID       *uuid.UUID `json:"driver_id,omitempty"`
	Status         TripStatus `json:"status"`
	Source         Location   `json:"source"`
	Destination    Location   `json:"destination"`
	EstimatedPrice Price      `json:"estimated_price"`
	FinalPrice     *Price     `json:"final_price,omitempty"`
	CreatedAt      time.Time  `json:"created_at"`
	AcceptedAt     *time.Time `json:"accepted_at,omitempty"`
	CompletedAt    *time.Time `json:"completed_at,omitempty"`
	OfferDeadline  *time.Time `json:"offer_deadline,omitempty"`
}

func (t *Trip) AcceptByDriver(driverID uuid.UUID) error {
	if t.Status != StatusRequested {
		return ErrInvalidTripStatus
	}
	// Allow if no driver assigned, or if the same driver is confirming a match
	if t.DriverID != nil && *t.DriverID != driverID {
		return ErrDriverAlreadySet
	}

	t.DriverID = &driverID
	t.Status = StatusAccepted
	now := time.Now()
	t.AcceptedAt = &now
	return nil
}

func (t *Trip) Start() error {
	if t.Status != StatusAccepted {
		return ErrInvalidTripStatus
	}
	t.Status = StatusStarted
	return nil
}

func (t *Trip) Complete(finalPrice float64) error {
	if t.Status != StatusStarted {
		return ErrInvalidTripStatus
	}
	t.Status = StatusCompleted
	now := time.Now()
	t.CompletedAt = &now
	t.FinalPrice = &Price{Amount: finalPrice, Currency: t.EstimatedPrice.Currency}
	return nil
}

func (t *Trip) RejectOffer(driverID uuid.UUID) error {
	if t.Status != StatusRequested && t.Status != StatusAccepted {
		return ErrInvalidTripStatus
	}
	
	// Ensure the driver rejecting is the one assigned
	if t.DriverID == nil || *t.DriverID != driverID {
		return errors.New("driver not assigned to this trip")
	}

	t.DriverID = nil
	t.Status = StatusRequested
	return nil
}

func (t *Trip) Cancel(reason string) error {
	if t.Status == StatusCompleted || t.Status == StatusCancelled || t.Status == StatusStarted {
		return ErrInvalidTripStatus
	}
	
	t.Status = StatusCancelled
	return nil
}

type CreateTripRequest struct {
	SourceLat      float64 `json:"source_lat" binding:"required"`
	SourceLng      float64 `json:"source_lng" binding:"required"`
	DestLat        float64 `json:"dest_lat" binding:"required"`
	DestLng        float64 `json:"dest_lng" binding:"required"`
	EstimatedPrice float64 `json:"estimated_price" binding:"required"`
	Currency       string  `json:"currency"`
}

type TripResponse struct {
	TripID uuid.UUID  `json:"trip_id"`
	Status TripStatus `json:"status"`
}

