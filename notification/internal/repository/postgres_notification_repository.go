package repository

import (
	"context"
	"database/sql"
)

type PostgresNotificationRepository struct {
	db *sql.DB
}

func NewPostgresNotificationRepository(db *sql.DB) *PostgresNotificationRepository {
	return &PostgresNotificationRepository{db: db}
}

func (r *PostgresNotificationRepository) SaveEvent(ctx context.Context, msgID, eventType, aggType, aggID, payload string) error {
	_, err := r.db.ExecContext(ctx,
		"INSERT INTO notification_history (event_id, event_type, aggregate_type, aggregate_id, payload) VALUES ($1, $2, $3, $4, $5)",
		msgID, eventType, aggType, aggID, payload)
	return err
}

func (r *PostgresNotificationRepository) GetHistory(ctx context.Context, limit int) ([]NotificationEvent, error) {
	rows, err := r.db.QueryContext(ctx,
		"SELECT event_id, event_type, aggregate_type, aggregate_id, payload, created_at FROM notification_history ORDER BY created_at DESC LIMIT $1",
		limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var events []NotificationEvent
	for rows.Next() {
		var e NotificationEvent
		if err := rows.Scan(&e.ID, &e.EventType, &e.AggregateType, &e.AggregateID, &e.Payload, &e.CreatedAt); err != nil {
			continue
		}
		events = append(events, e)
	}
	return events, nil
}
