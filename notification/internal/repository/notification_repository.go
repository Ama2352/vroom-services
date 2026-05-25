package repository

import (
	"context"
	"time"
)

type NotificationEvent struct {
	ID            string
	EventType     string
	AggregateType string
	AggregateID   string
	Payload       string
	CreatedAt     time.Time
}

type NotificationRepository interface {
	SaveEvent(ctx context.Context, msgID, eventType, aggType, aggID, payload string) error
	GetHistory(ctx context.Context, limit int) ([]NotificationEvent, error)
}
