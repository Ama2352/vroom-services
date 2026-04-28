package main

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
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
	dsn := fmt.Sprintf("host=%s port=%s user=%s password=%s dbname=%s sslmode=disable search_path=rides",
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
	ctx, stop := context.WithCancel(context.Background())
	defer stop()

	outboxWorker := worker.NewOutboxWorker(rideRepo, rdb, "ride_events")
	go outboxWorker.Start(ctx)

	updateWorker := worker.NewTripUpdateWorker(rdb, rideRepo, "ride_events", "ride_update_group", uuid.New().String())
	go updateWorker.Start(ctx)

	timeoutWorker := worker.NewTripTimeoutWorker(rideRepo, 10*time.Second, 60)
	go timeoutWorker.Start(ctx)

	// 5. Router Setup
	r := gin.Default()
	r.Use(handler.CorrelationMiddleware())


	// Probes
	r.GET("/healthz", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "UP"})
	})

	r.GET("/readyz", func(c *gin.Context) {
		dbErr := db.PingContext(c.Request.Context())
		redisErr := rdb.Ping(c.Request.Context()).Err()

		if dbErr != nil || redisErr != nil {
			c.JSON(http.StatusServiceUnavailable, gin.H{
				"status": "DOWN",
				"db":     dbErr == nil,
				"redis":  redisErr == nil,
			})
			return
		}
		c.JSON(http.StatusOK, gin.H{"status": "READY"})
	})

	// CORS middleware
	r.Use(func(c *gin.Context) {
		c.Writer.Header().Set("Access-Control-Allow-Origin", "*")
		c.Writer.Header().Set("Access-Control-Allow-Methods", "POST, GET, OPTIONS, PUT, DELETE")
		c.Writer.Header().Set("Access-Control-Allow-Headers", "Content-Type, Content-Length, Accept-Encoding, X-CSRF-Token, Authorization, accept, origin, Cache-Control, X-Requested-With, X-Correlation-ID, X-User-ID")
		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(204)
			return
		}
		c.Next()
	})

	v1 := r.Group("/v1")
	{
		trips := v1.Group("/trips")
		{
			trips.POST("", rideHandler.RequestRide)
			trips.GET("/:id", rideHandler.GetTrip)
			trips.POST("/:id/complete", rideHandler.CompleteTrip)
			trips.POST("/:id/accept", rideHandler.AcceptTrip)
			trips.POST("/:id/start", rideHandler.StartTrip)
			trips.POST("/:id/cancel", rideHandler.CancelTrip)
			trips.GET("/health", rideHandler.Health)
		}
	}

	// Server setup
	srv := &http.Server{
		Addr:    ":" + port,
		Handler: r,
	}

	go func() {
		log.Printf("Ride Service starting on port %s", port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("Listen: %s\n", err)
		}
	}()

	// Wait for interrupt signal to gracefully shutdown
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, os.Interrupt, syscall.SIGTERM)
	<-quit
	log.Println("Shutting down Ride Service...")

	stop() // Cancel worker contexts
	
	ctxShutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctxShutdown); err != nil {
		log.Fatal("Server forced to shutdown:", err)
	}

	log.Println("Ride Service exited gracefully")
}


func getEnv(key, fallback string) string {
	if value, ok := os.LookupEnv(key); ok {
		return value
	}
	return fallback
}
