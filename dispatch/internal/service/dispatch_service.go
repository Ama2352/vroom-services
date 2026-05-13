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
	// Search for nearest drivers within 5km (up to 10 candidates to account for exclusions)
	results, err := s.redisClient.GeoRadius(ctx, "drivers_location", lng, lat, &redis.GeoRadiusQuery{
		Radius:      15,
		Unit:        "km",
		WithDist:    true,
		WithCoord:   true,
		Count:       10,
		Sort:        "ASC",
	}).Result()

	if err != nil {
		return "", err
	}

	log.Printf("[DISPATCH] Found %d potential candidates for Trip %s", len(results), tripID)

	// Fetch rejected drivers for this trip
	rejectedDrivers, _ := s.redisClient.SMembers(ctx, "trip_rejections:"+tripID).Result()
	rejectedMap := make(map[string]bool)
	for _, id := range rejectedDrivers {
		rejectedMap[id] = true
	}

	var candidates []domain.AvailableDriver
	for _, r := range results {
		driverID := r.Name
		log.Printf("[DISPATCH] Checking candidate Driver %s at (%f, %f) for Trip %s", driverID, r.Latitude, r.Longitude, tripID)

		if rejectedMap[driverID] {
			log.Printf("[DISPATCH] Driver %s rejected Trip %s previously, skipping", driverID, tripID)
			continue
		}
		
		// Check if driver is still "fresh" (sent heartbeat recently)
		lastSeen, err := s.redisClient.Exists(ctx, "driver_last_seen:"+driverID).Result()
		if err != nil || lastSeen == 0 {
			if lastSeen == 0 {
				log.Printf("[CLEANUP] Driver %s is stale (no last_seen key), removing from geo index", driverID)
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

func (s *DispatchService) RecordRejection(ctx context.Context, tripID, driverID string) error {
	key := "trip_rejections:" + tripID
	err := s.redisClient.SAdd(ctx, key, driverID).Err()
	if err != nil {
		return err
	}
	// Expire rejection list after 1 hour to keep Redis clean
	s.redisClient.Expire(ctx, key, 1*time.Hour)
	return nil
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

func (s *DispatchService) Reset(ctx context.Context) error {
	log.Println("[DEBUG] Resetting Dispatch Service (FlushDB)")
	resetCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	return s.redisClient.FlushDB(resetCtx).Err()
}
