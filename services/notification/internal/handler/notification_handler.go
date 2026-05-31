package handler

import (
	"log"
	"net/http"
	"vroom-mvp/notification/internal/repository"
	"vroom-mvp/notification/internal/service"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"
)

var upgrader = websocket.Upgrader{
	ReadBufferSize:  1024,
	WriteBufferSize: 1024,
	CheckOrigin: func(r *http.Request) bool {
		return true
	},
}

type NotificationHandler struct {
	Hub  *service.Hub
	repo repository.NotificationRepository
}

func NewNotificationHandler(hub *service.Hub, repo repository.NotificationRepository) *NotificationHandler {
	return &NotificationHandler{Hub: hub, repo: repo}
}

func (h *NotificationHandler) HandleHistory(c *gin.Context) {
	events, err := h.repo.GetHistory(c.Request.Context(), 50)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	if events == nil {
		events = []repository.NotificationEvent{}
	}
	c.JSON(http.StatusOK, events)
}

func (h *NotificationHandler) HandleWS(c *gin.Context) {
	userID := c.Query("userId")
	conn, err := upgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		log.Printf("Failed to upgrade to WebSocket: %v", err)
		return
	}

	client := &service.Client{
		Hub:    h.Hub,
		Conn:   conn,
		Send:   make(chan []byte, 256),
		UserID: userID,
	}

	// Register the client
	h.Hub.RegisterClient(client)

	// Start the write pump in a separate goroutine
	go client.WritePump()
	
	// Read pump (we don't expect messages from the client in this service, but we need to detect disconnects)
	go func() {
		defer func() {
			h.Hub.UnregisterClient(client)
			conn.Close()
		}()
		for {
			_, _, err := conn.ReadMessage()
			if err != nil {
				break
			}
		}
	}()
}
