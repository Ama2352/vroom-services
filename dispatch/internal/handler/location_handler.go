package handler

import (
	"context"
	"log"
	"net/http"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"
	"github.com/redis/go-redis/v9"
)

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool {
		return true // For MVP, allow all origins
	},
}

type LocationHandler struct {
	redisClient *redis.Client
}

func NewLocationHandler(redisClient *redis.Client) *LocationHandler {
	return &LocationHandler{
		redisClient: redisClient,
	}
}

type LocationMessage struct {
	DriverID string  `json:"driver_id"`
	Lat      float64 `json:"lat"`
	Lng      float64 `json:"lng"`
}

func (h *LocationHandler) HandleWS(c *gin.Context) {
	conn, err := upgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		log.Printf("WebSocket upgrade failed: %v", err)
		return
	}
	defer conn.Close()

	for {
		var msg LocationMessage
		err := conn.ReadJSON(&msg)
		if err != nil {
			log.Printf("WebSocket read error: %v", err)
			break
		}

		// Update location in Redis Geo
		// Key: "drivers_location"
		err = h.redisClient.GeoAdd(context.Background(), "drivers_location", &redis.GeoLocation{
			Name:      msg.DriverID,
			Longitude: msg.Lng,
			Latitude:  msg.Lat,
		}).Err()

		if err != nil {
			log.Printf("Error updating Redis Geo: %v", err)
		} else {
			// Set expiration for location (e.g., 5 minutes) to avoid stale drivers
			// Redis GEO doesn't support TTL per member directly easily, but we can set TTL for the whole key
			// Better: store last updated time in a separate hash if needed, but for MVP GeoAdd is enough.
			log.Printf("Updated location for driver: %s (%f, %f)", msg.DriverID, msg.Lat, msg.Lng)
		}
	}
}
