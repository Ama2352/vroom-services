package domain

import (
	"time"

	"github.com/google/uuid"
)

type TripStatus string

const (
	StatusRequested  TripStatus = "REQUESTED"
	StatusAccepted   TripStatus = "ACCEPTED"
	StatusStarted    TripStatus = "STARTED"
	StatusCompleted  TripStatus = "COMPLETED"
	StatusCancelled  TripStatus = "CANCELLED"
)

type Trip struct {
	ID             uuid.UUID  `json:"id"`
	PassengerID    uuid.UUID  `json:"passenger_id"`
	DriverID       *uuid.UUID `json:"driver_id,omitempty"`
	Status         TripStatus `json:"status"`
	SourceLat      float64    `json:"source_lat"`
	SourceLng      float64    `json:"source_lng"`
	DestLat        float64    `json:"dest_lat"`
	DestLng        float64    `json:"dest_lng"`
	EstimatedPrice float64    `json:"estimated_price"`
	FinalPrice     *float64   `json:"final_price,omitempty"`
	CreatedAt      time.Time  `json:"created_at"`
	AcceptedAt     *time.Time `json:"accepted_at,omitempty"`
	CompletedAt    *time.Time `json:"completed_at,omitempty"`
}

type CreateTripRequest struct {
	SourceLat      float64 `json:"source_lat" binding:"required"`
	SourceLng      float64 `json:"source_lng" binding:"required"`
	DestLat        float64 `json:"dest_lat" binding:"required"`
	DestLng        float64 `json:"dest_lng" binding:"required"`
	EstimatedPrice float64 `json:"estimated_price" binding:"required"`
}

type TripResponse struct {
	TripID uuid.UUID  `json:"trip_id"`
	Status TripStatus `json:"status"`
}
