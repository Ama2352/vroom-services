package main

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"database/sql"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"
	"vroom-mvp/user/internal/handler"
	"vroom-mvp/user/internal/repository"
	"vroom-mvp/user/internal/service"
	"vroom-mvp/user/internal/util"

	"github.com/gin-gonic/gin"
	_ "github.com/lib/pq"
)

func main() {
	// 1. Configuration
	port := getEnv("PORT", "8080")
	dbHost := getEnv("DB_HOST", "localhost")
	dbPort := getEnv("DB_PORT", "5432")
	dbUser := getEnv("DB_USER", "vroom")
	dbPassword := getEnv("DB_PASSWORD", "vroom_dev")
	dbName := getEnv("DB_NAME", "vroom")

	// 2. Database connection
	dsn := fmt.Sprintf("host=%s port=%s user=%s password=%s dbname=%s sslmode=disable search_path=users",
		dbHost, dbPort, dbUser, dbPassword, dbName)
	
	db, err := sql.Open("postgres", dsn)
	if err != nil {
		log.Fatalf("Failed to open database: %v", err)
	}
	defer db.Close()

	// Wait for DB to be ready
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	
	for i := 0; i < 10; i++ {
		err = db.PingContext(ctx)
		if err == nil {
			break
		}
		log.Printf("Waiting for database... (%d/10)", i+1)
		time.Sleep(2 * time.Second)
	}
	if err != nil {
		log.Fatalf("Database not ready: %v", err)
	}

	// 3. Security: RSA Keys for JWT (RS256)
	// In production, these should be loaded from secrets/files
	privateKey, publicKey, err := loadOrGenerateKeys()
	if err != nil {
		log.Fatalf("Failed to initialize security keys: %v", err)
	}

	jwtManager := util.NewJWTManager(privateKey, publicKey, 24*time.Hour)

	// 4. Initialize Layers
	userRepo := repository.NewPostgresUserRepository(db)
	authService := service.NewAuthService(userRepo, jwtManager)
	authHandler := handler.NewAuthHandler(authService)

	// 5. Router Setup
	r := gin.Default()

	v1 := r.Group("/v1")
	{
		auth := v1.Group("/auth")
		{
			auth.POST("/register", authHandler.Register)
			auth.POST("/login", authHandler.Login)
			auth.GET("/health", authHandler.Health)
		}
	}

	// Root health check
	r.GET("/health", func(c *gin.Context) {
		c.String(http.StatusOK, "OK")
	})

	log.Printf("User Service starting on port %s", port)
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

func loadOrGenerateKeys() (*rsa.PrivateKey, *rsa.PublicKey, error) {
	// For MVP/Dev, we generate a new pair on startup if not provided
	// In a real app, you'd load these from a file or environment variable
	log.Println("Generating ephemeral RSA keys for dev environment...")
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return nil, nil, err
	}
	return privateKey, &privateKey.PublicKey, nil
}
