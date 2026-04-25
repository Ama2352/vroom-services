package service

import (
	"context"
	"log"

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
	// Search for nearest driver within 5km
	drivers, err := s.redisClient.GeoRadius(ctx, "drivers_location", lng, lat, &redis.GeoRadiusQuery{
		Radius:      5,
		Unit:        "km",
		WithDist:    true,
		WithCoord:   true,
		Count:       1,
		Sort:        "ASC",
	}).Result()

	if err != nil {
		return "", err
	}

	if len(drivers) == 0 {
		return "", nil // No drivers found
	}

	bestDriver := drivers[0]
	log.Printf("Matched Trip %s with Driver %s (Distance: %f km)", tripID, bestDriver.Name, bestDriver.Dist)

	return bestDriver.Name, nil
}
