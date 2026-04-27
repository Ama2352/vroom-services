package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"time"
	"vroom-mvp/notification/internal/handler"
	"vroom-mvp/notification/internal/service"
	"vroom-mvp/notification/internal/worker"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	_ "github.com/lib/pq"
	"github.com/redis/go-redis/v9"
	"database/sql"
	"fmt"
)

func main() {
	// 1. Configuration
	port := getEnv("PORT", "8080")
	redisAddr := getEnv("REDIS_ADDR", "localhost:6379")
	dbHost := getEnv("DB_HOST", "localhost")
	dbPort := getEnv("DB_PORT", "5432")
	dbUser := getEnv("DB_USER", "vroom")
	dbPass := getEnv("DB_PASSWORD", "vroom")
	dbName := getEnv("DB_NAME", "vroom")
	consumerID := uuid.New().String()

	// 2. Database connection
	dsn := fmt.Sprintf("host=%s port=%s user=%s password=%s dbname=%s sslmode=disable search_path=notifications", 
		dbHost, dbPort, dbUser, dbPass, dbName)
	db, err := sql.Open("postgres", dsn)
	if err != nil {
		log.Fatalf("Failed to open DB: %v", err)
	}
	defer db.Close()

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
	if err := db.PingContext(ctx); err != nil {
		log.Fatalf("Database not ready: %v", err)
	}

	// 4. Initialize Hub and WebSocket
	hub := service.NewHub()
	go hub.Run()
	notificationHandler := handler.NewNotificationHandler(hub)

	// 5. Start Notification Worker (Background)
	worker := worker.NewNotificationWorker(rdb, db, "ride_events", "notification_group", consumerID, hub)
	go worker.Start(context.Background())

	// 4. Router Setup
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
	r.Static("/static", "./static")

	v1 := r.Group("/v1")
	{
		v1.GET("/history", func(c *gin.Context) {
			rows, err := db.QueryContext(c.Request.Context(), 
				"SELECT event_id as id, event_type, aggregate_type, aggregate_id, payload, created_at FROM notification_history ORDER BY created_at DESC LIMIT 50")
			if err != nil {
				c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
				return
			}
			defer rows.Close()

			var events []map[string]interface{}
			for rows.Next() {
				var id, eventType, aggType, aggID, payloadStr string
				var createdAt time.Time
				if err := rows.Scan(&id, &eventType, &aggType, &aggID, &payloadStr, &createdAt); err != nil {
					continue
				}
				events = append(events, map[string]interface{}{
					"id":             id,
					"event_type":     eventType,
					"aggregate_type": aggType,
					"aggregate_id":   aggID,
					"payload":        payloadStr,
					"created_at":     createdAt,
				})
			}
			c.JSON(http.StatusOK, events)
		})

		// WebSocket endpoint
		v1.GET("/ws", notificationHandler.HandleWS)
	}

	r.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "UP", "consumer_id": consumerID})
	})

	log.Printf("Notification Service starting on port %s", port)
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
