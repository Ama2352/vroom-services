package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"log"
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
		SourceLat:      trip.Source.Point.Lat,
		SourceLng:      trip.Source.Point.Lng,
		DestLat:        trip.Destination.Point.Lat,
		DestLng:        trip.Destination.Point.Lng,
		EstimatedPrice: trip.EstimatedPrice.Amount,
		Currency: sql.NullString{String: func() string {
			if trip.EstimatedPrice.Currency == "" {
				return "VND"
			}
			return trip.EstimatedPrice.Currency
		}(), Valid: true},
		SourceAddress: sql.NullString{String: trip.Source.Address, Valid: trip.Source.Address != ""},
		DestAddress:   sql.NullString{String: trip.Destination.Address, Valid: trip.Destination.Address != ""},
		CreatedAt:     sql.NullTime{Time: trip.CreatedAt, Valid: true},
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
	if err != nil {
		return err
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

func (r *PostgresTripRepository) UpdateDriver(ctx context.Context, tripID uuid.UUID, driverID uuid.UUID) error {
	return r.queries.UpdateTripDriver(ctx, db.UpdateTripDriverParams{
		ID:       tripID,
		DriverID: uuid.NullUUID{UUID: driverID, Valid: true},
	})
}

func (r *PostgresTripRepository) AcceptTrip(ctx context.Context, tripID uuid.UUID, driverID uuid.UUID) error {
	return r.queries.AcceptTrip(ctx, db.AcceptTripParams{
		ID:       tripID,
		DriverID: uuid.NullUUID{UUID: driverID, Valid: true},
		Status:   string(domain.StatusAccepted),
	})
}

func (r *PostgresTripRepository) StartWithOutbox(ctx context.Context, tripID uuid.UUID, event *OutboxEvent) error {
	tx, err := r.conn.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	qtx := r.queries.WithTx(tx)

	// 1. Update Trip Status
	err = qtx.UpdateTripStatus(ctx, db.UpdateTripStatusParams{
		ID:     tripID,
		Status: string(domain.StatusStarted),
	})
	if err != nil {
		return err
	}

	// 2. Create Outbox Event
	payload, _ := json.Marshal(event.Payload)
	err = qtx.CreateOutboxEvent(ctx, db.CreateOutboxEventParams{
		ID:            event.ID,
		AggregateType: event.AggregateType,
		AggregateID:   event.AggregateID,
		EventType:     event.EventType,
		Payload:       payload,
		Status:        sql.NullString{String: "PENDING", Valid: true},
		CreatedAt:     sql.NullTime{Time: time.Now(), Valid: true},
		CorrelationID: sql.NullString{String: event.CorrelationID, Valid: event.CorrelationID != ""},
	})
	if err != nil {
		return err
	}

	return tx.Commit()
}

func (r *PostgresTripRepository) AcceptWithOutbox(ctx context.Context, tripID uuid.UUID, driverID uuid.UUID, event *OutboxEvent) error {
	tx, err := r.conn.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	qtx := r.queries.WithTx(tx)

	// 1. Update Trip
	err = qtx.AcceptTrip(ctx, db.AcceptTripParams{
		ID:       tripID,
		DriverID: uuid.NullUUID{UUID: driverID, Valid: true},
		Status:   string(domain.StatusAccepted),
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
		CorrelationID: sql.NullString{String: event.CorrelationID, Valid: event.CorrelationID != ""},
	})
	if err != nil {

		return err
	}

	return tx.Commit()
}

func (r *PostgresTripRepository) CompleteTrip(ctx context.Context, tripID uuid.UUID, finalPrice float64) error {
	return r.queries.CompleteTrip(ctx, db.CompleteTripParams{
		ID:         tripID,
		Status:     string(domain.StatusCompleted),
		FinalPrice: sql.NullFloat64{Float64: finalPrice, Valid: true},
	})
}

func (r *PostgresTripRepository) CompleteWithOutbox(ctx context.Context, tripID uuid.UUID, finalPrice float64, event *OutboxEvent) error {
	tx, err := r.conn.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	qtx := r.queries.WithTx(tx)

	// 1. Update Trip
	err = qtx.CompleteTrip(ctx, db.CompleteTripParams{
		ID:         tripID,
		Status:     string(domain.StatusCompleted),
		FinalPrice: sql.NullFloat64{Float64: finalPrice, Valid: true},
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
		CorrelationID: sql.NullString{String: event.CorrelationID, Valid: event.CorrelationID != ""},
	})

	if err != nil {
		return err
	}

	return tx.Commit()
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
			CorrelationID: row.CorrelationID.String,
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

func (r *PostgresTripRepository) GetStuckTrips(ctx context.Context, timeout time.Time) ([]*domain.Trip, error) {
	rows, err := r.queries.GetStuckTrips(ctx, sql.NullTime{Time: timeout, Valid: true})
	if err != nil {
		return nil, err
	}

	trips := make([]*domain.Trip, len(rows))
	for i, row := range rows {
		trips[i] = toDomainTrip(row)
	}
	return trips, nil
}

func (r *PostgresTripRepository) CancelWithOutbox(ctx context.Context, tripID uuid.UUID, event *OutboxEvent) error {
	tx, err := r.conn.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	qtx := r.queries.WithTx(tx)

	// 1. Update Trip Status
	err = qtx.UpdateTripStatus(ctx, db.UpdateTripStatusParams{
		ID:     tripID,
		Status: string(domain.StatusCancelled),
	})
	if err != nil {
		return err
	}

	// 2. Create Outbox Event
	payload, _ := json.Marshal(event.Payload)
	err = qtx.CreateOutboxEvent(ctx, db.CreateOutboxEventParams{
		ID:            event.ID,
		AggregateType: event.AggregateType,
		AggregateID:   event.AggregateID,
		EventType:     event.EventType,
		Payload:       payload,
		Status:        sql.NullString{String: "PENDING", Valid: true},
		CreatedAt:     sql.NullTime{Time: time.Now(), Valid: true},
		CorrelationID: sql.NullString{String: event.CorrelationID, Valid: event.CorrelationID != ""},
	})
	if err != nil {

		return err
	}

	return tx.Commit()
}

func (r *PostgresTripRepository) RejectOfferWithOutbox(ctx context.Context, tripID uuid.UUID, event *OutboxEvent) error {
	tx, err := r.conn.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	qtx := r.queries.WithTx(tx)

	// 1. Update Trip: Clear Driver and reset Status
	err = qtx.UpdateTripDriver(ctx, db.UpdateTripDriverParams{
		ID:       tripID,
		DriverID: uuid.NullUUID{Valid: false},
	})
	if err != nil {
		return err
	}

	err = qtx.UpdateTripStatus(ctx, db.UpdateTripStatusParams{
		ID:     tripID,
		Status: string(domain.StatusRequested),
	})
	if err != nil {
		return err
	}

	// 2. Create Outbox Event
	payload, _ := json.Marshal(event.Payload)
	err = qtx.CreateOutboxEvent(ctx, db.CreateOutboxEventParams{
		ID:            event.ID,
		AggregateType: event.AggregateType,
		AggregateID:   event.AggregateID,
		EventType:     event.EventType,
		Payload:       payload,
		Status:        sql.NullString{String: "PENDING", Valid: true},
		CreatedAt:     sql.NullTime{Time: time.Now(), Valid: true},
	})
	if err != nil {
		return err
	}

	return tx.Commit()
}

func (r *PostgresTripRepository) IsEventProcessed(ctx context.Context, id uuid.UUID) (bool, error) {
	return r.queries.IsEventProcessed(ctx, id)
}

func (r *PostgresTripRepository) MarkEventProcessed(ctx context.Context, id uuid.UUID, eventType string) error {
	return r.queries.MarkEventProcessed(ctx, db.MarkEventProcessedParams{
		ID:        id,
		EventType: eventType,
	})
}

func toDomainTrip(t db.Trip) *domain.Trip {

	trip := &domain.Trip{

		ID:          t.ID,
		PassengerID: t.PassengerID,
		Status:      domain.TripStatus(t.Status),
		Source: domain.Location{
			Point:   domain.GeoPoint{Lat: t.SourceLat, Lng: t.SourceLng},
			Address: t.SourceAddress.String,
		},
		Destination: domain.Location{
			Point:   domain.GeoPoint{Lat: t.DestLat, Lng: t.DestLng},
			Address: t.DestAddress.String,
		},
		EstimatedPrice: domain.Price{
			Amount:   t.EstimatedPrice,
			Currency: t.Currency.String,
		},
		CreatedAt: t.CreatedAt.Time,
	}
	if t.DriverID.Valid {
		uid := t.DriverID.UUID
		trip.DriverID = &uid
	}
	if t.FinalPrice.Valid {
		trip.FinalPrice = &domain.Price{
			Amount:   t.FinalPrice.Float64,
			Currency: t.Currency.String,
		}
	}
	if t.AcceptedAt.Valid {
		trip.AcceptedAt = &t.AcceptedAt.Time
	}
	if t.CompletedAt.Valid {
		trip.CompletedAt = &t.CompletedAt.Time
	}
	return trip
}
func (r *PostgresTripRepository) Reset(ctx context.Context) error {
	log.Println("[DEBUG] Resetting Ride Repository (Deleting all records)")
	tx, err := r.conn.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	// Delete in correct order to respect FKs (if any)
	tables := []string{"inbox_events", "outbox_events", "trips"}
	for _, table := range tables {
		_, err := tx.ExecContext(ctx, "DELETE FROM "+table)
		if err != nil {
			log.Printf("[RESET ERROR] Failed to delete from %s: %v", table, err)
			return err
		}
	}

	return tx.Commit()
}
