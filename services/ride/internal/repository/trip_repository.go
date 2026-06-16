package repository

import (
	"context"
	"time"
	"vroom-mvp/ride/internal/domain"

	"github.com/google/uuid"
)


type OutboxEvent struct {
	ID            uuid.UUID
	AggregateType string
	AggregateID   uuid.UUID
	EventType     string
	Payload       interface{}
	CorrelationID string
	Traceparent   string
}


type TripRepository interface {
	CreateWithOutbox(ctx context.Context, trip *domain.Trip, event *OutboxEvent) error
	GetByID(ctx context.Context, id uuid.UUID) (*domain.Trip, error)
	UpdateStatus(ctx context.Context, tripID uuid.UUID, status domain.TripStatus) error
	UpdateDriver(ctx context.Context, tripID uuid.UUID, driverID uuid.UUID) error
	AcceptTrip(ctx context.Context, tripID uuid.UUID, driverID uuid.UUID) error
	AcceptWithOutbox(ctx context.Context, tripID uuid.UUID, driverID uuid.UUID, event *OutboxEvent) error
	StartWithOutbox(ctx context.Context, tripID uuid.UUID, event *OutboxEvent) error
	CompleteTrip(ctx context.Context, tripID uuid.UUID, finalPrice float64) error
	CompleteWithOutbox(ctx context.Context, tripID uuid.UUID, finalPrice float64, event *OutboxEvent) error
	GetUnpublishedEvents(ctx context.Context, limit int) ([]*OutboxEvent, error)
	UpdateEventStatus(ctx context.Context, id uuid.UUID, status string) error
	GetStuckTrips(ctx context.Context, timeout time.Time) ([]*domain.Trip, error)
	SetOfferDeadline(ctx context.Context, tripID uuid.UUID, deadline time.Time) error
	GetExpiredOffers(ctx context.Context, cutoff time.Time) ([]*domain.Trip, error)
	GetStuckAcceptedTrips(ctx context.Context, cutoff time.Time) ([]*domain.Trip, error)
	CancelWithOutbox(ctx context.Context, tripID uuid.UUID, event *OutboxEvent) error
	RejectOfferWithOutbox(ctx context.Context, tripID uuid.UUID, event *OutboxEvent) error
	IsEventProcessed(ctx context.Context, id uuid.UUID) (bool, error)
	MarkEventProcessed(ctx context.Context, id uuid.UUID, eventType string) error
	Reset(ctx context.Context) error
}



