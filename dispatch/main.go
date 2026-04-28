package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
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
	workerCtx, stopWorker := context.WithCancel(context.Background())
	defer stopWorker()
	
	consumer := worker.NewRideEventConsumer(rdb, dispatchService, "ride_events", "dispatch_group", consumerID)
	go consumer.Start(workerCtx)

	// 5. Router Setup
	r := gin.Default()

	r.GET("/healthz", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "UP"})
	})

	r.GET("/readyz", func(c *gin.Context) {
		if err := rdb.Ping(c.Request.Context()).Err(); err != nil {
			c.JSON(http.StatusServiceUnavailable, gin.H{"status": "DOWN", "redis": false})
			return
		}
		c.JSON(http.StatusOK, gin.H{"status": "READY"})
	})

	r.Use(func(c *gin.Context) {
		c.Writer.Header().Set("Access-Control-Allow-Origin", "*")
		c.Writer.Header().Set("Access-Control-Allow-Methods", "POST, GET, OPTIONS, PUT, DELETE")
		c.Writer.Header().Set("Access-Control-Allow-Headers", "Content-Type, Content-Length, Accept-Encoding, X-CSRF-Token, Authorization, accept, origin, Cache-Control, X-Requested-With, X-Correlation-ID")
		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(204)
			return
		}
		c.Next()
	})

	v1 := r.Group("/v1")
	{
		v1.PUT("/drivers/:id/location", locationHandler.UpdateLocation)
		dispatch := v1.Group("/dispatch")
		{
			dispatch.GET("/ws/location", locationHandler.HandleWS)
		}
	}

	srv := &http.Server{
		Addr:    ":" + port,
		Handler: r,
	}

	go func() {
		log.Printf("Dispatch Service starting on port %s", port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("Listen: %s\n", err)
		}
	}()

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, os.Interrupt, syscall.SIGTERM)
	<-quit
	log.Println("Shutting down Dispatch Service...")

	stopWorker()
	
	ctxShutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctxShutdown); err != nil {
		log.Fatal("Server forced to shutdown:", err)
	}

	log.Println("Dispatch Service exited gracefully")
}


func getEnv(key, fallback string) string {
	if value, ok := os.LookupEnv(key); ok {
		return value
	}
	return fallback
}
