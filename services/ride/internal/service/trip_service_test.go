package service

import (
    "context"
    "testing"

    "vroom-mvp/ride/internal/domain"
    "vroom-mvp/ride/internal/repository"
    "github.com/google/uuid"
)

type capturingRepo struct {
    repository.TripRepository
    event *repository.OutboxEvent
}

func (r *capturingRepo) CreateWithOutbox(_ context.Context, _ *domain.Trip, event *repository.OutboxEvent) error {
    r.event = event
    return nil
}

func validRequest() domain.CreateTripRequest {
    return domain.CreateTripRequest{SourceLat: 1, SourceLng: 2, DestLat: 3, DestLng: 4, EstimatedPrice: 10, Currency: "USD"}
}

func TestRequestTripUsesConfiguredContract(t *testing.T) {
    tests := []struct{ version, want string }{{"v1", "Trip.Requested"}, {"v2", "Trip.Requested.v2"}}
    for _, tt := range tests {
        t.Run(tt.version, func(t *testing.T) {
            repo := &capturingRepo{}
            svc := NewTripService(repo, tt.version)
            if _, err := svc.RequestTrip(context.Background(), uuid.New(), validRequest()); err != nil {
                t.Fatalf("RequestTrip() error = %v", err)
            }
            if repo.event.EventType != tt.want {
                t.Fatalf("event type = %q, want %q", repo.event.EventType, tt.want)
            }
        })
    }
}

func TestRequestedEventTypeRejectsUnknownVersion(t *testing.T) {
    if _, err := requestedEventType("v3"); err == nil {
        t.Fatal("requestedEventType(v3) returned nil error")
    }
}
