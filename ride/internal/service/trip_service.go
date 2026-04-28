package service

import (
	"context"
	"errors"
	"time"
	"vroom-mvp/ride/internal/domain"
	"vroom-mvp/ride/internal/repository"

	"github.com/google/uuid"
)


type TripService struct {
	repo repository.TripRepository
}

func NewTripService(repo repository.TripRepository) *TripService {
	return &TripService{
		repo: repo,
	}
}

func (s *TripService) RequestTrip(ctx context.Context, passengerID uuid.UUID, req domain.CreateTripRequest) (*domain.Trip, error) {
	trip := &domain.Trip{
		ID:          uuid.New(),
		PassengerID: passengerID,
		Status:      domain.StatusRequested,
		Source: domain.Location{
			Point: domain.GeoPoint{Lat: req.SourceLat, Lng: req.SourceLng},
		},
		Destination: domain.Location{
			Point: domain.GeoPoint{Lat: req.DestLat, Lng: req.DestLng},
		},
		EstimatedPrice: domain.Price{
			Amount:   req.EstimatedPrice,
			Currency: req.Currency,
		},
		CreatedAt: time.Now(),
	}

	event := &repository.OutboxEvent{
		ID:            uuid.New(),
		AggregateType: "TRIP",
		AggregateID:   trip.ID,
		EventType:     "Trip.Requested",
		Payload: map[string]interface{}{
			"id":              trip.ID,
			"passenger_id":    trip.PassengerID,
			"status":          trip.Status,
			"source_lat":      trip.Source.Point.Lat,
			"source_lng":      trip.Source.Point.Lng,
			"dest_lat":        trip.Destination.Point.Lat,
			"dest_lng":        trip.Destination.Point.Lng,
			"estimated_price": trip.EstimatedPrice.Amount,
			"currency":        trip.EstimatedPrice.Currency,
			"created_at":      trip.CreatedAt,
		},
	}

	err := s.repo.CreateWithOutbox(ctx, trip, event)
	if err != nil {
		return nil, err
	}

	return trip, nil
}

func (s *TripService) GetTrip(ctx context.Context, id uuid.UUID) (*domain.Trip, error) {
	return s.repo.GetByID(ctx, id)
}

func (s *TripService) CompleteTrip(ctx context.Context, tripID uuid.UUID, finalPrice float64) error {
	trip, err := s.repo.GetByID(ctx, tripID)
	if err != nil {
		return err
	}
	if trip == nil {
		return errors.New("trip not found")
	}

	if err := trip.Complete(finalPrice); err != nil {
		return err
	}

	// Use recorded events to create Outbox Event
	event := &repository.OutboxEvent{
		ID:            uuid.New(),
		AggregateType: "TRIP",
		AggregateID:   tripID,
		EventType:     "Trip.Completed",
		Payload: map[string]interface{}{
			"id":          tripID,
			"final_price": finalPrice,
			"status":      "COMPLETED",
			"updated_at":  time.Now(),
		},
	}

	return s.repo.CompleteWithOutbox(ctx, tripID, finalPrice, event)
}

func (s *TripService) AcceptTrip(ctx context.Context, tripID uuid.UUID, driverID uuid.UUID) error {
	trip, err := s.repo.GetByID(ctx, tripID)
	if err != nil {
		return err
	}
	if trip == nil {
		return errors.New("trip not found")
	}

	if err := trip.AcceptByDriver(driverID); err != nil {
		return err
	}

	// Use recorded events to create Outbox Event
	event := &repository.OutboxEvent{
		ID:            uuid.New(),
		AggregateType: "TRIP",
		AggregateID:   tripID,
		EventType:     "Trip.Accepted",
		Payload: map[string]interface{}{
			"id":         tripID,
			"driver_id":  driverID,
			"status":     "ACCEPTED",
			"updated_at": time.Now(),
		},
	}

	return s.repo.AcceptWithOutbox(ctx, tripID, driverID, event)
}

func (s *TripService) StartTrip(ctx context.Context, tripID uuid.UUID) error {
	trip, err := s.repo.GetByID(ctx, tripID)
	if err != nil {
		return err
	}
	if trip == nil {
		return errors.New("trip not found")
	}

	if err := trip.Start(); err != nil {
		return err
	}

	event := &repository.OutboxEvent{
		ID:            uuid.New(),
		AggregateType: "TRIP",
		AggregateID:   tripID,
		EventType:     "Trip.Started",
		Payload: map[string]interface{}{
			"id":         tripID,
			"status":     "IN_PROGRESS",
			"updated_at": time.Now(),
		},
	}

	return s.repo.StartWithOutbox(ctx, tripID, event)
}

func (s *TripService) CancelTrip(ctx context.Context, tripID uuid.UUID, reason string) error {
	trip, err := s.repo.GetByID(ctx, tripID)
	if err != nil {
		return err
	}
	if trip == nil {
		return errors.New("trip not found")
	}

	if err := trip.Cancel(reason); err != nil {
		return err
	}

	event := &repository.OutboxEvent{
		ID:            uuid.New(),
		AggregateType: "TRIP",
		AggregateID:   tripID,
		EventType:     "Trip.Cancelled",
		Payload: map[string]interface{}{
			"id":         tripID,
			"status":     "CANCELLED",
			"reason":     reason,
			"updated_at": time.Now(),
		},
	}

	return s.repo.CancelWithOutbox(ctx, tripID, event)
}

