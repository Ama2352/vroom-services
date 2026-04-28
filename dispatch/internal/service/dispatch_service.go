package service

import (
	"context"
	"log"
	"time"
	"vroom-mvp/dispatch/internal/domain"

	"github.com/redis/go-redis/v9"
)


type DispatchService struct {
	redisClient *redis.Client
}

func NewDispatchService(redisClient *redis.Client) *DispatchService {
	return &DispatchService{
		redisClient: redisClient,
	}
}

func (s *DispatchService) MatchDriver(ctx context.Context, tripID string, lat, lng float64) (string, error) {
	// Search for nearest drivers within 5km (up to 5 candidates)
	results, err := s.redisClient.GeoRadius(ctx, "drivers_location", lng, lat, &redis.GeoRadiusQuery{
		Radius:      5,
		Unit:        "km",
		WithDist:    true,
		Count:       5,
		Sort:        "ASC",
	}).Result()

	if err != nil {
		return "", err
	}

	var candidates []domain.AvailableDriver
	for _, r := range results {
		driverID := r.Name
		
		// Check if driver is still "fresh" (sent heartbeat recently)
		lastSeen, err := s.redisClient.Exists(ctx, "driver_last_seen:"+driverID).Result()
		if err != nil || lastSeen == 0 {
			if lastSeen == 0 {
				log.Printf("[CLEANUP] Driver %s is stale, removing from geo index", driverID)
				s.redisClient.ZRem(ctx, "drivers_location", driverID)
			}
			continue
		}

		candidates = append(candidates, domain.AvailableDriver{
			ID:       driverID,
			Lat:      r.Latitude,
			Lng:      r.Longitude,
			Distance: r.Dist,
		})
	}

	pool := domain.NewDriverPool(candidates)
	bestMatch, err := pool.WaterfallMatch()
	if err != nil {
		return "", nil // No drivers found
	}

	log.Printf("Matched Trip %s with Fresh Driver %s (Distance: %f km)", tripID, bestMatch.ID, bestMatch.Distance)
	return bestMatch.ID, nil
}


func (s *DispatchService) UpdateDriverLocation(ctx context.Context, driverID string, lat, lng float64) error {
	// 1. Update Geo Location
	err := s.redisClient.GeoAdd(ctx, "drivers_location", &redis.GeoLocation{
		Name:      driverID,
		Latitude:  lat,
		Longitude: lng,
	}).Err()
	if err != nil {
		return err
	}

	// 2. Update Freshness (30s TTL)
	err = s.redisClient.Set(ctx, "driver_last_seen:"+driverID, "active", 30*time.Second).Err()
	if err != nil {
		return err
	}

	log.Printf("[HEARTBEAT] Driver %s location updated: %f, %f", driverID, lat, lng)
	return nil
}
