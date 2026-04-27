package service

import (
	"encoding/json"
	"log"
	"sync"

	"github.com/gorilla/websocket"
)

// Client represents a connected user/session
type Client struct {
	Hub    *Hub
	Conn   *websocket.Conn
	Send   chan []byte
	UserID string // Added to support targeted notifications
}

// Hub maintains the set of active clients and broadcasts messages
type Hub struct {
	clients     map[*Client]bool
	userClients map[string]map[*Client]bool // Map UserID to their active connections
	broadcast   chan []byte
	register    chan *Client
	unregister  chan *Client
	mu          sync.Mutex
}

func NewHub() *Hub {
	return &Hub{
		broadcast:   make(chan []byte),
		register:    make(chan *Client),
		unregister:  make(chan *Client),
		clients:     make(map[*Client]bool),
		userClients: make(map[string]map[*Client]bool),
	}
}

func (h *Hub) Run() {
	for {
		select {
		case client := <-h.register:
			h.mu.Lock()
			h.clients[client] = true
			if client.UserID != "" {
				if _, ok := h.userClients[client.UserID]; !ok {
					h.userClients[client.UserID] = make(map[*Client]bool)
				}
				h.userClients[client.UserID][client] = true
			}
			h.mu.Unlock()
			log.Printf("Client registered to Notification Hub (User: %s)", client.UserID)

		case client := <-h.unregister:
			h.mu.Lock()
			if _, ok := h.clients[client]; ok {
				delete(h.clients, client)
				if client.UserID != "" {
					if clients, ok := h.userClients[client.UserID]; ok {
						delete(clients, client)
						if len(clients) == 0 {
							delete(h.userClients, client.UserID)
						}
					}
				}
				close(client.Send)
			}
			h.mu.Unlock()
			log.Printf("Client unregistered from Notification Hub (User: %s)", client.UserID)

		case message := <-h.broadcast:
			h.mu.Lock()
			for client := range h.clients {
				select {
				case client.Send <- message:
				default:
					h.doUnregister(client)
				}
			}
			h.mu.Unlock()
		}
	}
}

// doUnregister is a helper to clean up a client without needing the lock (caller must hold it)
func (h *Hub) doUnregister(client *Client) {
	if _, ok := h.clients[client]; ok {
		delete(h.clients, client)
		if client.UserID != "" {
			if clients, ok := h.userClients[client.UserID]; ok {
				delete(clients, client)
				if len(clients) == 0 {
					delete(h.userClients, client.UserID)
				}
			}
		}
		close(client.Send)
	}
}

// RegisterClient adds a new client to the hub
func (h *Hub) RegisterClient(c *Client) {
	h.register <- c
}

// UnregisterClient removes a client from the hub
func (h *Hub) UnregisterClient(c *Client) {
	h.unregister <- c
}

// BroadcastEvent takes a generic event and sends it to all clients
func (h *Hub) BroadcastEvent(event interface{}) {
	data, err := json.Marshal(event)
	if err != nil {
		log.Printf("Error marshaling event for broadcast: %v", err)
		return
	}
	h.broadcast <- data
}

// SendToUser sends a message to all connections for a specific user
func (h *Hub) SendToUser(userID string, event interface{}) {
	data, err := json.Marshal(event)
	if err != nil {
		log.Printf("Error marshaling event for user %s: %v", userID, err)
		return
	}

	h.mu.Lock()
	defer h.mu.Unlock()

	if clients, ok := h.userClients[userID]; ok {
		for client := range clients {
			select {
			case client.Send <- data:
			default:
				h.doUnregister(client)
			}
		}
	}
}

// WritePump pumps messages from the hub to the websocket connection.
func (c *Client) WritePump() {
	defer func() {
		c.Conn.Close()
	}()
	for {
		select {
		case message, ok := <-c.Send:
			if !ok {
				c.Conn.WriteMessage(websocket.CloseMessage, []byte{})
				return
			}
			if err := c.Conn.WriteMessage(websocket.TextMessage, message); err != nil {
				return
			}
		}
	}
}
