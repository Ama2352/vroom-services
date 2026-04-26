package handler

import (
	"log"
	"net/http"
	"vroom-mvp/dispatch/internal/service"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"
)

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool {
		return true // For MVP, allow all origins
	},
}

type LocationHandler struct {
	service *service.DispatchService
}

func NewLocationHandler(service *service.DispatchService) *LocationHandler {
	return &LocationHandler{
		service: service,
	}
}

type LocationUpdate struct {
	Lat float64 `json:"lat" binding:"required"`
	Lng float64 `json:"lng" binding:"required"`
}

type LocationMessage struct {
	DriverID string  `json:"driver_id"`
	Lat      float64 `json:"lat"`
	Lng      float64 `json:"lng"`
}

func (h *LocationHandler) UpdateLocation(c *gin.Context) {
	driverID := c.Param("id")
	var req LocationUpdate
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	err := h.service.UpdateDriverLocation(c.Request.Context(), driverID, req.Lat, req.Lng)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update location"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"status": "ok"})
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

		// Update location via Service
		err = h.service.UpdateDriverLocation(c.Request.Context(), msg.DriverID, msg.Lat, msg.Lng)
		if err != nil {
			log.Printf("Error updating driver location via WS: %v", err)
		}
	}
}
