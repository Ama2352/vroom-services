package domain

import (
	"errors"
	"time"

	"github.com/google/uuid"
)

var (
	ErrAlreadyDelivered = errors.New("notification already delivered")
)

type NotificationType string

const (
	TypeRideRequested NotificationType = "RIDE_REQUESTED"
	TypeRideOffered   NotificationType = "RIDE_OFFERED"
	TypeRideAssigned  NotificationType = "RIDE_ASSIGNED"
	TypeRideAccepted  NotificationType = "RIDE_ACCEPTED"
	TypeRideStarted   NotificationType = "RIDE_STARTED"
	TypeRideCompleted NotificationType = "RIDE_COMPLETED"
	TypeRideCancelled NotificationType = "RIDE_CANCELLED"
)

type NotificationStatus string

const (
	StatusQueued    NotificationStatus = "QUEUED"
	StatusSent      NotificationStatus = "SENT"
	StatusDelivered NotificationStatus = "DELIVERED"
	StatusFailed    NotificationStatus = "FAILED"
)

type DeliveryChannel string

const (
	ChannelWebSocket DeliveryChannel = "WEBSOCKET"
	ChannelPush      DeliveryChannel = "PUSH"
	ChannelSMS       DeliveryChannel = "SMS"
)

type Notification struct {
	ID        uuid.UUID          `json:"id"`
	UserID    uuid.UUID          `json:"user_id"`
	Type      NotificationType   `json:"type"`
	Status    NotificationStatus `json:"status"`
	Payload   interface{}        `json:"payload"`
	Channel   DeliveryChannel    `json:"channel"`
	CreatedAt time.Time          `json:"created_at"`
	UpdatedAt time.Time          `json:"updated_at"`
}

func (n *Notification) MarkDelivered(channel DeliveryChannel) error {
	if n.Status == StatusDelivered {
		return ErrAlreadyDelivered
	}
	n.Status = StatusDelivered
	n.Channel = channel
	n.UpdatedAt = time.Now()
	return nil
}
