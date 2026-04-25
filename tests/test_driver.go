package main

import (
	"log"
	"net/url"
	"time"

	"github.com/gorilla/websocket"
)

func main() {
	u := url.URL{Scheme: "ws", Host: "localhost:8083", Path: "/v1/dispatch/ws/location"}
	log.Printf("Connecting to %s", u.String())

	c, _, err := websocket.DefaultDialer.Dial(u.String(), nil)
	if err != nil {
		log.Fatal("Dial error:", err)
	}
	defer c.Close()

	// Driver 1: Near the test user (Test User is at 10.7626, 106.6601)
	msg := map[string]interface{}{
		"driver_id": "driver_1",
		"lat":       10.7630,
		"lng":       106.6610,
	}

	for i := 0; i < 5; i++ {
		err := c.WriteJSON(msg)
		if err != nil {
			log.Println("Write error:", err)
			return
		}
		log.Printf("Sent location for driver_1")
		time.Sleep(2 * time.Second)
	}
}
