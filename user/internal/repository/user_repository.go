package repository

import (
	"context"
	"vroom-mvp/user/internal/domain"

	"github.com/google/uuid"
)

type OutboxEvent struct {
	ID            uuid.UUID
	AggregateType string
	AggregateID   uuid.UUID
	EventType     string
	Payload       interface{}
}

type UserRepository interface {
	CreateWithOutbox(ctx context.Context, user *domain.User, event *OutboxEvent) error
	GetByID(ctx context.Context, id uuid.UUID) (*domain.User, error)
	GetByEmail(ctx context.Context, email domain.Email) (*domain.User, error)
	Update(ctx context.Context, user *domain.User) error
}

