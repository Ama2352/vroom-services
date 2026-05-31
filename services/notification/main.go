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

	"vroom-mvp/notification/internal/handler"
	"vroom-mvp/notification/internal/repository"
	"vroom-mvp/notification/internal/service"
	"vroom-mvp/notification/internal/worker"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	_ "github.com/lib/pq"
	"github.com/redis/go-redis/v9"
	ginprometheus "github.com/zsais/go-gin-prometheus"
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

	// 3. Redis connection
	rdb := redis.NewClient(&redis.Options{
		Addr: redisAddr,
	})
	defer rdb.Close()

	// Wait for dependencies
	waitCtx, waitCancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer waitCancel()

	if err := rdb.Ping(waitCtx).Err(); err != nil {
		log.Fatalf("Redis not ready: %v", err)
	}
	if err := db.PingContext(waitCtx); err != nil {
		log.Fatalf("Database not ready: %v", err)
	}

	// 4. Initialize layers
	notifRepo := repository.NewPostgresNotificationRepository(db)
	hub := service.NewHub()
	go hub.Run()
	notificationHandler := handler.NewNotificationHandler(hub, notifRepo)

	// 5. Start worker
	workerCtx, stopWorker := context.WithCancel(context.Background())
	defer stopWorker()

	notifWorker := worker.NewNotificationWorker(rdb, notifRepo, "ride_events", "notification_group", consumerID, hub)
	go notifWorker.Start(workerCtx)

	// 6. Router setup
	r := gin.Default()

	p := ginprometheus.NewPrometheus("gin")
	p.Use(r)

	r.Use(func(c *gin.Context) {
		c.Writer.Header().Set("Access-Control-Allow-Origin", "*")
		c.Writer.Header().Set("Access-Control-Allow-Methods", "POST, GET, OPTIONS, PUT, DELETE")
		c.Writer.Header().Set("Access-Control-Allow-Headers", "Content-Type, Content-Length, Accept-Encoding, X-CSRF-Token, Authorization, accept, origin, Cache-Control, X-Requested-With")
		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(204)
			return
		}
		c.Next()
	})

	r.Static("/static", "./static")

	r.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "UP", "consumer_id": consumerID})
	})

	r.GET("/readyz", func(c *gin.Context) {
		redisErr := rdb.Ping(c.Request.Context()).Err()
		dbErr := db.PingContext(c.Request.Context())
		if redisErr != nil || dbErr != nil {
			c.JSON(http.StatusServiceUnavailable, gin.H{
				"status": "DOWN",
				"redis":  redisErr == nil,
				"db":     dbErr == nil,
			})
			return
		}
		c.JSON(http.StatusOK, gin.H{"status": "READY"})
	})

	v1 := r.Group("/v1")
	v1.Use(handler.JWTMiddleware())
	{
		v1.GET("/history", notificationHandler.HandleHistory)
		v1.GET("/ws", notificationHandler.HandleWS)
	}

	// 7. Start server + graceful shutdown
	srv := &http.Server{
		Addr:    ":" + port,
		Handler: r,
	}

	go func() {
		log.Printf("Notification Service starting on port %s", port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("Listen: %s\n", err)
		}
	}()

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, os.Interrupt, syscall.SIGTERM)
	<-quit
	log.Println("Shutting down Notification Service...")

	stopWorker()

	ctxShutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctxShutdown); err != nil {
		log.Fatal("Server forced to shutdown:", err)
	}

	log.Println("Notification Service exited gracefully")
}

func getEnv(key, fallback string) string {
	if value, ok := os.LookupEnv(key); ok {
		return value
	}
	return fallback
}
