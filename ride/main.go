package main

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"
	"vroom-mvp/ride/internal/handler"
	"vroom-mvp/ride/internal/repository"
	"vroom-mvp/ride/internal/service"
	"vroom-mvp/ride/internal/worker"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	_ "github.com/lib/pq"
	"github.com/redis/go-redis/v9"
)

func main() {
	// 1. Configuration
	port := getEnv("PORT", "8080")
	dbHost := getEnv("DB_HOST", "localhost")
	dbPort := getEnv("DB_PORT", "5432")
	dbUser := getEnv("DB_USER", "vroom")
	dbPassword := getEnv("DB_PASSWORD", "vroom_dev")
	dbName := getEnv("DB_NAME", "vroom")
	redisAddr := getEnv("REDIS_ADDR", "localhost:6379")

	// 2. Database connection
	dsn := fmt.Sprintf("host=%s port=%s user=%s password=%s dbname=%s sslmode=disable",
		dbHost, dbPort, dbUser, dbPassword, dbName)
	
	db, err := sql.Open("postgres", dsn)
	if err != nil {
		log.Fatalf("Failed to open database: %v", err)
	}
	defer db.Close()

	// 3. Redis connection
	rdb := redis.NewClient(&redis.Options{
		Addr: redisAddr,
	})
	defer rdb.Close()

	// Wait for services
	waitCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	
	if err := db.PingContext(waitCtx); err != nil {
		log.Fatalf("Database not ready: %v", err)
	}
	if err := rdb.Ping(waitCtx).Err(); err != nil {
		log.Fatalf("Redis not ready: %v", err)
	}

	// 4. Initialize Layers
	rideRepo := repository.NewPostgresTripRepository(db)
	rideService := service.NewTripService(rideRepo)
	rideHandler := handler.NewTripHandler(rideService)

	// 4. Start Workers (Background)
	outboxWorker := worker.NewOutboxWorker(rideRepo, rdb, "ride_events")
	go outboxWorker.Start(context.Background())

	updateWorker := worker.NewTripUpdateWorker(rdb, rideRepo, "ride_events", "ride_update_group", uuid.New().String())
	go updateWorker.Start(context.Background())

	// 5. Router Setup
	r := gin.Default()

	v1 := r.Group("/v1")
	{
		trips := v1.Group("/trips")
		{
			trips.POST("", rideHandler.RequestRide)
			trips.GET("/:id", rideHandler.GetTrip)
			trips.GET("/health", rideHandler.Health)
		}
	}

	// Root health check
	r.GET("/health", func(c *gin.Context) {
		c.String(http.StatusOK, "OK")
	})

	log.Printf("Ride Service starting on port %s", port)
	if err := r.Run(":" + port); err != nil {
		log.Fatalf("Failed to start server: %v", err)
	}
}

func getEnv(key, fallback string) string {
	if value, ok := os.LookupEnv(key); ok {
		return value
	}
	return fallback
}
