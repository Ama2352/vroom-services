package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"time"
	"vroom-mvp/ride/internal/domain"
	"vroom-mvp/ride/internal/repository/db"

	"github.com/google/uuid"
)

type PostgresTripRepository struct {
	conn    *sql.DB
	queries *db.Queries
}

func NewPostgresTripRepository(conn *sql.DB) *PostgresTripRepository {
	return &PostgresTripRepository{
		conn:    conn,
		queries: db.New(conn),
	}
}

func (r *PostgresTripRepository) CreateWithOutbox(ctx context.Context, trip *domain.Trip, event *OutboxEvent) error {
	tx, err := r.conn.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	qtx := r.queries.WithTx(tx)

	// 1. Create Trip
	err = qtx.CreateTrip(ctx, db.CreateTripParams{
		ID:             trip.ID,
		PassengerID:    trip.PassengerID,
		Status:         string(trip.Status),
		SourceLat:      trip.SourceLat,
		SourceLng:      trip.SourceLng,
		DestLat:        trip.DestLat,
		DestLng:        trip.DestLng,
		EstimatedPrice: trip.EstimatedPrice,
		CreatedAt:      sql.NullTime{Time: trip.CreatedAt, Valid: true},
	})
	if err != nil {
		return err
	}

	// 2. Create Outbox Event
	payload, err := json.Marshal(event.Payload)
	if err != nil {
		return err
	}

	err = qtx.CreateOutboxEvent(ctx, db.CreateOutboxEventParams{
		ID:            event.ID,
		AggregateType: event.AggregateType,
		AggregateID:   event.AggregateID,
		EventType:     event.EventType,
		Payload:       payload,
		Status:        sql.NullString{String: "PENDING", Valid: true},
		CreatedAt:     sql.NullTime{Time: time.Now(), Valid: true},
	})
	// Fixing CreatedAt for outbox event
	if _, ok := event.Payload.(*domain.Trip); ok {
		// use current time if payload is trip
	}

	return tx.Commit()
}

func (r *PostgresTripRepository) GetByID(ctx context.Context, id uuid.UUID) (*domain.Trip, error) {
	row, err := r.queries.GetTrip(ctx, id)
	if err != nil {
		if err == sql.ErrNoRows {
			return nil, nil
		}
		return nil, err
	}

	return toDomainTrip(row), nil
}

func (r *PostgresTripRepository) UpdateStatus(ctx context.Context, tripID uuid.UUID, status domain.TripStatus) error {
	return r.queries.UpdateTripStatus(ctx, db.UpdateTripStatusParams{
		ID:     tripID,
		Status: string(status),
	})
}

func (r *PostgresTripRepository) AcceptTrip(ctx context.Context, tripID uuid.UUID, driverID uuid.UUID) error {
	return r.queries.AcceptTrip(ctx, db.AcceptTripParams{
		ID:       tripID,
		DriverID: uuid.NullUUID{UUID: driverID, Valid: true},
		Status:   string(domain.StatusAccepted),
	})
}

func (r *PostgresTripRepository) GetUnpublishedEvents(ctx context.Context, limit int) ([]*OutboxEvent, error) {
	rows, err := r.queries.GetUnpublishedEvents(ctx, int32(limit))
	if err != nil {
		return nil, err
	}

	events := make([]*OutboxEvent, len(rows))
	for i, row := range rows {
		events[i] = &OutboxEvent{
			ID:            row.ID,
			AggregateType: row.AggregateType,
			AggregateID:   row.AggregateID,
			EventType:     row.EventType,
			Payload:       row.Payload,
		}
	}
	return events, nil
}

func (r *PostgresTripRepository) UpdateEventStatus(ctx context.Context, id uuid.UUID, status string) error {
	return r.queries.UpdateEventStatus(ctx, db.UpdateEventStatusParams{
		ID:     id,
		Status: sql.NullString{String: status, Valid: true},
	})
}

func toDomainTrip(t db.Trip) *domain.Trip {
	trip := &domain.Trip{
		ID:             t.ID,
		PassengerID:    t.PassengerID,
		Status:         domain.TripStatus(t.Status),
		SourceLat:      t.SourceLat,
		SourceLng:      t.SourceLng,
		DestLat:        t.DestLat,
		DestLng:        t.DestLng,
		EstimatedPrice: t.EstimatedPrice,
		CreatedAt:      t.CreatedAt.Time,
	}
	if t.DriverID.Valid {
		uid := t.DriverID.UUID
		trip.DriverID = &uid
	}
	if t.FinalPrice.Valid {
		trip.FinalPrice = &t.FinalPrice.Float64
	}
	if t.AcceptedAt.Valid {
		trip.AcceptedAt = &t.AcceptedAt.Time
	}
	if t.CompletedAt.Valid {
		trip.CompletedAt = &t.CompletedAt.Time
	}
	return trip
}
