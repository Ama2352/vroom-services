package service

import (
	"context"
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
		ID:             uuid.New(),
		PassengerID:    passengerID,
		Status:         domain.StatusRequested,
		SourceLat:      req.SourceLat,
		SourceLng:      req.SourceLng,
		DestLat:        req.DestLat,
		DestLng:        req.DestLng,
		EstimatedPrice: req.EstimatedPrice,
		CreatedAt:      time.Now(),
	}

	event := &repository.OutboxEvent{
		ID:            uuid.New(),
		AggregateType: "TRIP",
		AggregateID:   trip.ID,
		EventType:     "Trip.Requested",
		Payload:       trip,
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
