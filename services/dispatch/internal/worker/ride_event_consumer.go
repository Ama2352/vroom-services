package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"time"
	"vroom-mvp/dispatch/internal/service"

	"github.com/google/uuid"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"
)

const (
	maxEventRetries = 3
	dlqStreamName   = "ride_events_dlq"
)

var dlqEventsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
	Name: "vroom_dlq_events_total",
	Help: "Total number of events sent to the DLQ after exhausting retries",
}, []string{"event_type"})

type RideEventConsumer struct {
	redisClient     *redis.Client
	dispatchService *service.DispatchService
	streamName      string
	groupName       string
	consumerID      string
}

func NewRideEventConsumer(redisClient *redis.Client, dispatchService *service.DispatchService, streamName, groupName, consumerID string) *RideEventConsumer {
	return &RideEventConsumer{
		redisClient:     redisClient,
		dispatchService: dispatchService,
		streamName:      streamName,
		groupName:       groupName,
		consumerID:      consumerID,
	}
}

func (c *RideEventConsumer) Start(ctx context.Context) {
	// Create consumer group if not exists
	err := c.redisClient.XGroupCreateMkStream(ctx, c.streamName, c.groupName, "0").Err()
	if err != nil && err.Error() != "BUSYGROUP Consumer Group name already exists" {
		log.Printf("Error creating consumer group: %v", err)
	}

	log.Printf("Dispatch consumer started, listening on stream: %s, group: %s", c.streamName, c.groupName)

	for {
		select {
		case <-ctx.Done():
			return
		default:
			c.consume(ctx)
		}
	}
}

func (c *RideEventConsumer) consume(ctx context.Context) {
	// Reclaim messages stuck in PEL > 30 s (handles consumer crash mid-processing)
	autoclaimed, _, _ := c.redisClient.XAutoClaim(ctx, &redis.XAutoClaimArgs{
		Stream:   c.streamName,
		Group:    c.groupName,
		Consumer: c.consumerID,
		MinIdle:  30 * time.Second,
		Start:    "0-0",
		Count:    10,
	}).Result()
	for _, msg := range autoclaimed {
		eventID := ""
		if val, ok := msg.Values["id"]; ok {
			eventID = val.(string)
		} else {
			eventID = msg.ID
		}
		key := "processed_event:dispatch:" + eventID
		setErr := c.redisClient.SetArgs(ctx, key, "true", redis.SetArgs{
			TTL:  24 * time.Hour,
			Mode: "NX",
		}).Err()
		if setErr != nil && setErr != redis.Nil {
			log.Printf("[DISPATCH ERROR] Failed to check idempotency for reclaimed event %s: %v", eventID, setErr)
		}
		if setErr == nil {
			c.processWithDLQ(ctx, msg)
		} else if setErr == redis.Nil {
			log.Printf("[IDEMPOTENCY] Reclaimed event %s already processed by dispatch, skipping", eventID)
		}
		c.redisClient.XAck(ctx, c.streamName, c.groupName, msg.ID)
	}

	// Read from group
	entries, err := c.redisClient.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group:    c.groupName,
		Consumer: c.consumerID,
		Streams:  []string{c.streamName, ">"},
		Count:    10,
		Block:    5 * time.Second,
	}).Result()

	if err != nil {
		if err != redis.Nil {
			log.Printf("Error reading from Redis Stream: %v", err)
			// If group doesn't exist, try to recreate it
			if strings.Contains(err.Error(), "NOGROUP") {
				log.Printf("[RECOVERY] Consumer group 'dispatch_group' missing (NOGROUP). Attempting to recreate...")
				if err := c.redisClient.XGroupCreateMkStream(ctx, c.streamName, c.groupName, "0").Err(); err != nil {
					log.Printf("[RECOVERY ERROR] Failed to recreate group: %v", err)
				} else {
					log.Printf("[RECOVERY SUCCESS] Consumer group 'dispatch_group' recreated.")
				}
			}
		}
		return
	}

	for _, stream := range entries {
		for _, message := range stream.Messages {
			// Idempotency check using SETNX on Event ID
			eventID := ""
			if val, ok := message.Values["id"]; ok {
				eventID = val.(string)
			} else {
				eventID = message.ID // Fallback to stream ID
			}

			key := "processed_event:dispatch:" + eventID
			setErr := c.redisClient.SetArgs(ctx, key, "true", redis.SetArgs{
				TTL:  24 * time.Hour,
				Mode: "NX",
			}).Err()
			if setErr != nil && setErr != redis.Nil {
				log.Printf("[DISPATCH ERROR] Failed to check idempotency for event %s: %v", eventID, setErr)
			} else if setErr == redis.Nil {
				log.Printf("[IDEMPOTENCY] Event %s already processed by dispatch, skipping", eventID)
				c.redisClient.XAck(ctx, c.streamName, c.groupName, message.ID)
				continue
			}

			c.processWithDLQ(ctx, message)
			// Acknowledge message
			c.redisClient.XAck(ctx, c.streamName, c.groupName, message.ID)
		}
	}
}


func (c *RideEventConsumer) processWithDLQ(ctx context.Context, msg redis.XMessage) {
	retryKey := "event:retry:" + msg.ID
	retries, _ := c.redisClient.Incr(ctx, retryKey).Result()
	c.redisClient.Expire(ctx, retryKey, 24*time.Hour)

	c.handleMessage(ctx, msg)

	eventType := ""
	if v, ok := msg.Values["type"]; ok {
		eventType = v.(string)
	}
	known := eventType == "Trip.Requested" || eventType == "Trip.OfferRejected" ||
		eventType == "Trip.Accepted" || eventType == "Trip.Cancelled" ||
		eventType == "Trip.Completed" || eventType == "Trip.Matched" ||
		eventType == "Trip.MatchFailed"

	if !known && retries >= maxEventRetries {
		payload := ""
		if v, ok := msg.Values["payload"]; ok {
			payload = v.(string)
		}
		_ = c.redisClient.XAdd(ctx, &redis.XAddArgs{
			Stream: dlqStreamName,
			Values: map[string]any{
				"original_id": msg.ID,
				"event_type":  eventType,
				"error":       fmt.Sprintf("unknown event type after %d retries", retries),
				"payload":     payload,
				"failed_at":   time.Now().Format(time.RFC3339),
			},
		}).Err()
		_ = c.redisClient.Del(ctx, retryKey).Err()
		dlqEventsTotal.WithLabelValues(eventType).Inc()
		log.Printf("[DLQ] Event %s (type=%s) moved to DLQ after %d retries", msg.ID, eventType, retries)
	}
}

func (c *RideEventConsumer) handleMessage(ctx context.Context, msg redis.XMessage) {
	// Extract trace context propagated from the Outbox publisher
	tracer := otel.Tracer("dispatch-consumer")
	carrier := propagation.MapCarrier{}
	if tp, ok := msg.Values["traceparent"]; ok && tp != nil {
		carrier["traceparent"] = tp.(string)
	}
	if ts, ok := msg.Values["tracestate"]; ok && ts != nil {
		carrier["tracestate"] = ts.(string)
	}
	remoteCtx := otel.GetTextMapPropagator().Extract(context.Background(), carrier)
	remoteSpan := trace.SpanContextFromContext(remoteCtx)

	eventType := ""
	if v, ok := msg.Values["type"]; ok {
		eventType = v.(string)
	}
	ctx, span := tracer.Start(ctx, "dispatch.consume."+eventType,
		trace.WithLinks(trace.Link{SpanContext: remoteSpan}),
	)
	defer span.End()

	// Robust field extraction
	getVal := func(key string) string {
		if val, ok := msg.Values[key]; ok {
			return val.(string)
		}
		return ""
	}

	eventType = getVal("type")
	aggregateType := getVal("aggregate")
	aggregateID := getVal("aggregate_id")
	if aggregateID == "" {
		aggregateID = aggregateType // Fallback to "aggregate" key if ride service changed format
	}
	payload := getVal("payload")

	correlationID := getVal("correlation_id")
	log.Printf("[DEBUG] [%s] Dispatch Consumer: Received %s for %s (%s)", correlationID, eventType, aggregateType, aggregateID)


	if eventType == "Trip.Requested" || eventType == "Trip.OfferRejected" {
		var data map[string]interface{}
		if err := json.Unmarshal([]byte(payload), &data); err != nil {
			log.Printf("[ERROR] Dispatch Consumer: Failed to unmarshal payload for trip %s: %v", aggregateID, err)
			return
		}

		tripID := aggregateID
		if id, ok := data["id"].(string); ok {
			tripID = id
		}

		if eventType == "Trip.OfferRejected" {
			driverID, _ := data["driver_id"].(string)
			log.Printf("[REJECT] Driver %s rejected Trip %s. Recording rejection and re-matching...", driverID, tripID)
			_ = c.dispatchService.RecordRejection(ctx, tripID, driverID)
			// Saga compensation: release driver reservation so they can be matched again
			if driverID != "" {
				if err := c.dispatchService.ReleaseDriver(ctx, driverID); err != nil {
					log.Printf("[SAGA COMPENSATE] Failed to release driver %s: %v", driverID, err)
				} else {
					log.Printf("[SAGA COMPENSATE] Driver %s released (compensation for rejected offer)", driverID)
				}
			}
		}

		// Helper to extract float64 robustly
		asFloat := func(v interface{}) float64 {
			if f, ok := v.(float64); ok {
				return f
			}
			if i, ok := v.(int); ok {
				return float64(i)
			}
			if i64, ok := v.(int64); ok {
				return float64(i64)
			}
			return 0
		}

		lat := asFloat(data["source_lat"])
		lng := asFloat(data["source_lng"])

		log.Printf("[DISPATCH] Trip %s Payload: %s", tripID, payload)
		log.Printf("[DISPATCH] Extracted Coords: lat=%f, lng=%f", lat, lng)

		log.Printf("[STEP 2] Dispatcher matching driver for Trip: %s at (%f, %f)", tripID, lat, lng)

		// Match Driver
		driverID, err := c.dispatchService.MatchDriver(ctx, tripID, lat, lng)
		if err != nil {
			log.Printf("[DISPATCH ERROR] MatchDriver failed for trip %s: %v", tripID, err)
			return
		}

		if driverID == "" {
			log.Printf("[STEP 2.X] MATCH FAILED: No available drivers found within radius for trip: %s", tripID)

			// Publish Trip.MatchFailed event
			failedPayload := map[string]interface{}{
				"id":         tripID,
				"reason":     "NO_DRIVERS_AVAILABLE_AFTER_REJECTIONS",
				"updated_at": time.Now().Format(time.RFC3339),
			}
			payloadJSON, _ := json.Marshal(failedPayload)

			err = c.redisClient.XAdd(ctx, &redis.XAddArgs{
				Stream: c.streamName,
				Values: map[string]interface{}{
					"id":             uuid.New().String(),
					"type":           "Trip.MatchFailed",
					"aggregate":      "TRIP",
					"aggregate_id":   tripID,
					"payload":        string(payloadJSON),
					"correlation_id": correlationID,
				},
			}).Err()

			if err != nil {
				log.Printf("[DISPATCH ERROR] Failed to publish Trip.MatchFailed for trip %s: %v", tripID, err)
			}
			return
		}

		log.Printf("[STEP 2] MATCH SUCCESS: Trip %s assigned to Driver %s. Publishing 'Trip.Matched'...", tripID, driverID)

		// Publish Trip.Matched event
		matchPayload := map[string]interface{}{
			"id":           tripID,
			"driver_id":    driverID,
			"passenger_id": data["passenger_id"],
			"status":       "ACCEPTED",
			"updated_at":   time.Now().Format(time.RFC3339),
		}
		payloadJSON, _ := json.Marshal(matchPayload)

		err = c.redisClient.XAdd(ctx, &redis.XAddArgs{
			Stream: c.streamName,
			Values: map[string]interface{}{
				"id":             uuid.New().String(),
				"type":           "Trip.Matched",
				"aggregate":      "TRIP",
				"aggregate_id":   tripID,
				"payload":        string(payloadJSON),
				"correlation_id": correlationID,
			},
		}).Err()

		if err != nil {
			log.Printf("[DISPATCH ERROR] Failed to publish Trip.Matched for trip %s: %v", tripID, err)
		} else {
			log.Printf("[STEP 2.1] Event 'Trip.Matched' published successfully for Trip: %s", tripID)
			// Saga step 2: commit driver reservation in Redis
			if err := c.dispatchService.ReserveDriver(ctx, driverID); err != nil {
				log.Printf("[SAGA] Failed to reserve driver %s: %v", driverID, err)
			} else {
				log.Printf("[SAGA] Driver %s reserved (ON_OFFER) for Trip %s", driverID, tripID)
			}
		}
	} else if eventType == "Trip.Accepted" {
		var data map[string]interface{}
		if err := json.Unmarshal([]byte(payload), &data); err != nil {
			log.Printf("[ERROR] Dispatch Consumer: Failed to unmarshal Trip.Accepted payload: %v", err)
			return
		}
		driverID, _ := data["driver_id"].(string)
		if driverID != "" {
			// Saga step 4: transition ON_OFFER → ON_TRIP
			if err := c.dispatchService.ConfirmDriverOnTrip(ctx, driverID); err != nil {
				log.Printf("[SAGA] Failed to confirm driver %s ON_TRIP: %v", driverID, err)
			} else {
				log.Printf("[SAGA] Driver %s confirmed ON_TRIP", driverID)
			}
		}
	} else if eventType == "Trip.Cancelled" {
		var data map[string]interface{}
		if err := json.Unmarshal([]byte(payload), &data); err != nil {
			log.Printf("[ERROR] Dispatch Consumer: Failed to unmarshal Trip.Cancelled payload: %v", err)
			return
		}
		driverID, _ := data["driver_id"].(string)
		tripID := aggregateID
		if id, ok := data["id"].(string); ok {
			tripID = id
		}
		// Cleanup rejection tracking
		_ = c.redisClient.Del(ctx, "trip_rejections:"+tripID)
		// Saga compensation: release driver if one was assigned
		if driverID != "" {
			if err := c.dispatchService.ReleaseDriver(ctx, driverID); err != nil {
				log.Printf("[SAGA COMPENSATE] Failed to release driver %s on cancellation: %v", driverID, err)
			} else {
				log.Printf("[SAGA COMPENSATE] Driver %s released (Trip.Cancelled)", driverID)
			}
		}
	} else if eventType == "Trip.Completed" {
		var data map[string]interface{}
		if err := json.Unmarshal([]byte(payload), &data); err != nil {
			log.Printf("[ERROR] Dispatch Consumer: Failed to unmarshal Trip.Completed payload: %v", err)
			return
		}
		driverID, _ := data["driver_id"].(string)
		if driverID != "" {
			// Saga cleanup: remove ON_TRIP commitment after successful completion
			if err := c.dispatchService.ReleaseDriver(ctx, driverID); err != nil {
				log.Printf("[SAGA] Failed to release driver %s after completion: %v", driverID, err)
			} else {
				log.Printf("[SAGA] Driver %s released after Trip.Completed", driverID)
			}
		}
	}
}

// ConsumeOnce runs a single consume cycle — used by integration tests for deterministic control.
func (c *RideEventConsumer) ConsumeOnce(ctx context.Context) {
	c.consume(ctx)
}
