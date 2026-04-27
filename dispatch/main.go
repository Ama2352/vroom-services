package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"time"
	"vroom-mvp/dispatch/internal/handler"
	"vroom-mvp/dispatch/internal/service"
	"vroom-mvp/dispatch/internal/worker"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

func main() {
	// 1. Configuration
	port := getEnv("PORT", "8080")
	redisAddr := getEnv("REDIS_ADDR", "localhost:6379")
	consumerID := uuid.New().String()

	// 2. Redis connection
	rdb := redis.NewClient(&redis.Options{
		Addr: redisAddr,
	})
	defer rdb.Close()

	// Wait for Redis
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	
	if err := rdb.Ping(ctx).Err(); err != nil {
		log.Fatalf("Redis not ready: %v", err)
	}

	// 3. Initialize Services
	dispatchService := service.NewDispatchService(rdb)
	locationHandler := handler.NewLocationHandler(dispatchService)

	// 4. Start Event Consumer (Background)
	consumer := worker.NewRideEventConsumer(rdb, dispatchService, "ride_events", "dispatch_group", consumerID)
	go consumer.Start(context.Background())

	// 5. Router Setup
	r := gin.Default()

	r.Use(func(c *gin.Context) {
		c.Writer.Header().Set("Access-Control-Allow-Origin", "http://localhost:5173")
		c.Writer.Header().Set("Access-Control-Allow-Methods", "POST, GET, OPTIONS, PUT, DELETE")
		c.Writer.Header().Set("Access-Control-Allow-Headers", "Content-Type, Content-Length, Accept-Encoding, X-CSRF-Token, Authorization, accept, origin, Cache-Control, X-Requested-With")
		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(204)
			return
		}
		c.Next()
	})

	v1 := r.Group("/v1")
	{
		// Driver locations
		v1.PUT("/drivers/:id/location", locationHandler.UpdateLocation)
		
		dispatch := v1.Group("/dispatch")
		{
			// WebSocket for driver location updates
			dispatch.GET("/ws/location", locationHandler.HandleWS)
		}
	}

	r.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "UP", "consumer_id": consumerID})
	})

	log.Printf("Dispatch Service starting on port %s", port)
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
