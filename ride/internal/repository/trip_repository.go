package repository

import (
	"context"
	"vroom-mvp/ride/internal/domain"

	"github.com/google/uuid"
)

type OutboxEvent struct {
	ID            uuid.UUID
	AggregateType string
	AggregateID   uuid.UUID
	EventType     string
	Payload       interface{}
}

type TripRepository interface {
	CreateWithOutbox(ctx context.Context, trip *domain.Trip, event *OutboxEvent) error
	GetByID(ctx context.Context, id uuid.UUID) (*domain.Trip, error)
	UpdateStatus(ctx context.Context, tripID uuid.UUID, status domain.TripStatus) error
	AcceptTrip(ctx context.Context, tripID uuid.UUID, driverID uuid.UUID) error
	GetUnpublishedEvents(ctx context.Context, limit int) ([]*OutboxEvent, error)
	UpdateEventStatus(ctx context.Context, id uuid.UUID, status string) error
}
