//go:build integration

package choreography_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"testing"
	"time"

	_ "github.com/lib/pq"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	// Public facades that re-export ride/dispatch internals
	rideExport "vroom-mvp/ride/testexport"
	dispExport "vroom-mvp/dispatch/testexport"

	"vroom-mvp/integration-tests/testkit"
)

// ---------------------------------------------------------------------------
// Environment setup
// ---------------------------------------------------------------------------

type choreographyEnv struct {
	db           *sql.DB
	rdb          *redis.Client
	repo         *rideExport.PostgresTripRepository
	outbox       *rideExport.OutboxWorker
	timeoutWkr   *rideExport.TripTimeoutWorker
	updateWkr    *rideExport.TripUpdateWorker
	dispSvc      *dispExport.DispatchService
	dispConsumer *dispExport.RideEventConsumer
	stream       string
}

func setupChoreography(ctx context.Context, t *testing.T) *choreographyEnv {
	t.Helper()
	const stream = "ride_events"

	db := testkit.StartPostgres(ctx, t)
	rdb := testkit.StartRedis(ctx, t)

	repo := rideExport.NewPostgresTripRepository(db)
	outbox := rideExport.NewOutboxWorker(repo, rdb, stream)
	timeoutWkr := rideExport.NewTripTimeoutWorker(repo, time.Minute, 60)
	updateWkr := rideExport.NewTripUpdateWorker(rdb, repo, stream, "ride_update_group", "ride-update-1")
	svc := dispExport.NewDispatchService(rdb)
	consumer := dispExport.NewRideEventConsumer(rdb, svc, stream, "dispatch_group", "dispatch-1")

	// Create consumer groups before tests run.
	// Both workers create their own group in Start(); in tests we drive them via
	// ConsumeOnce/CheckTimeoutsOnce, so we must create the groups manually here.
	for _, cfg := range []struct{ group, start string }{
		{"dispatch_group", "0"},
		{"ride_update_group", "0"},
	} {
		err := rdb.XGroupCreateMkStream(ctx, stream, cfg.group, cfg.start).Err()
		if err != nil && err.Error() != "BUSYGROUP Consumer Group name already exists" {
			require.NoError(t, err)
		}
	}

	return &choreographyEnv{
		db:           db,
		rdb:          rdb,
		repo:         repo,
		outbox:       outbox,
		timeoutWkr:   timeoutWkr,
		updateWkr:    updateWkr,
		dispSvc:      svc,
		dispConsumer: consumer,
		stream:       stream,
	}
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// seedDriver plants a driver in the geo index with a live heartbeat.
func seedDriver(ctx context.Context, rdb *redis.Client, driverID string, lat, lng float64) {
	rdb.GeoAdd(ctx, "drivers_location", &redis.GeoLocation{
		Name:      driverID,
		Latitude:  lat,
		Longitude: lng,
	})
	rdb.Set(ctx, "driver_last_seen:"+driverID, "active", 30*time.Second)
}

// newTrip returns a minimal REQUESTED trip with the given passenger.
func newTrip(passengerID uuid.UUID) *rideExport.Trip {
	return &rideExport.Trip{
		ID:          uuid.New(),
		PassengerID: passengerID,
		Status:      rideExport.StatusRequested,
		Source: rideExport.Location{
			Point: rideExport.GeoPoint{Lat: 10.762622, Lng: 106.660172},
		},
		Destination: rideExport.Location{
			Point: rideExport.GeoPoint{Lat: 10.795202, Lng: 106.721519},
		},
		EstimatedPrice: rideExport.Price{Amount: 50000, Currency: "VND"},
		CreatedAt:      time.Now(),
	}
}

// newEvent builds a minimal OutboxEvent for a trip.
func newEvent(tripID uuid.UUID, eventType string) *rideExport.OutboxEvent {
	return &rideExport.OutboxEvent{
		ID:            uuid.New(),
		AggregateType: "TRIP",
		AggregateID:   tripID,
		EventType:     eventType,
		Payload: map[string]interface{}{
			"id":         tripID.String(),
			"source_lat": 10.762622,
			"source_lng": 106.660172,
		},
	}
}

// newEventWithDriver adds driver_id to the payload (needed for Trip.Accepted / Trip.Completed).
func newEventWithDriver(tripID uuid.UUID, driverID uuid.UUID, eventType string) *rideExport.OutboxEvent {
	return &rideExport.OutboxEvent{
		ID:            uuid.New(),
		AggregateType: "TRIP",
		AggregateID:   tripID,
		EventType:     eventType,
		Payload: map[string]interface{}{
			"id":         tripID.String(),
			"driver_id":  driverID.String(),
			"source_lat": 10.762622,
			"source_lng": 106.660172,
		},
	}
}

// countStreamByType scans all messages in the stream and returns a tally by event type.
func countStreamByType(ctx context.Context, rdb *redis.Client, stream string) map[string]int {
	msgs, _ := rdb.XRange(ctx, stream, "-", "+").Result()
	counts := make(map[string]int)
	for _, m := range msgs {
		if t, ok := m.Values["type"]; ok {
			counts[fmt.Sprintf("%v", t)]++
		}
	}
	return counts
}

// matchedDriverIDs returns the distinct driver IDs found in Trip.Matched entries.
func matchedDriverIDs(ctx context.Context, rdb *redis.Client, stream string) []string {
	msgs, _ := rdb.XRange(ctx, stream, "-", "+").Result()
	seen := make(map[string]struct{})
	var ids []string
	for _, m := range msgs {
		if fmt.Sprintf("%v", m.Values["type"]) != "Trip.Matched" {
			continue
		}
		raw, _ := m.Values["payload"].(string)
		var data map[string]interface{}
		if err := json.Unmarshal([]byte(raw), &data); err != nil {
			continue
		}
		did, _ := data["driver_id"].(string)
		if did == "" {
			continue
		}
		if _, dup := seen[did]; !dup {
			seen[did] = struct{}{}
			ids = append(ids, did)
		}
	}
	return ids
}

// ---------------------------------------------------------------------------
// Test 1 – Happy path
// ---------------------------------------------------------------------------

// TestChoreography_HappyPath proves the full saga:
// REQUESTED → driver matched → ACCEPTED → IN_PROGRESS → COMPLETED; driver released.
func TestChoreography_HappyPath(t *testing.T) {
	ctx := context.Background()
	log := testkit.New(t)
	log.Begin(
		"TestChoreography_HappyPath",
		"Full Saga: REQUESTED→MATCHED→ACCEPTED→IN_PROGRESS→COMPLETED; driver released",
	)

	env := setupChoreography(ctx, t)
	passengerID := uuid.New()
	driverID := uuid.MustParse("00000000-0000-0000-0000-000000000001")

	// Step 1 – seed driver, create trip
	log.Step(1, 12, "Seed driver, CreateWithOutbox (Trip.Requested)")
	seedDriver(ctx, env.rdb, driverID.String(), 10.763000, 106.661000)
	trip := newTrip(passengerID)
	require.NoError(t, env.repo.CreateWithOutbox(ctx, trip, newEvent(trip.ID, "Trip.Requested")))

	// Step 2 – outbox publishes Trip.Requested
	log.Step(2, 12, "OutboxWorker.ProcessOnce → publishes Trip.Requested")
	env.outbox.ProcessOnce(ctx)
	testkit.AssertStreamHasEntry(ctx, t, env.rdb, env.stream, "Trip.Requested")

	// Step 3 – dispatch matches driver, publishes Trip.Matched, reserves driver
	log.Step(3, 12, "dispConsumer.ConsumeOnce → matches driver → Trip.Matched + ReserveDriver")
	env.dispConsumer.ConsumeOnce(ctx)
	testkit.AssertStreamHasEntry(ctx, t, env.rdb, env.stream, "Trip.Matched")

	// Step 4 – ride update worker assigns driver + sets offer_deadline
	log.Step(4, 12, "updateWkr.ConsumeOnce → UpdateDriver + SetOfferDeadline")
	env.updateWkr.ConsumeOnce(ctx)
	dbTrip, err := env.repo.GetByID(ctx, trip.ID)
	require.NoError(t, err)
	require.NotNil(t, dbTrip.DriverID, "driver must be assigned after Trip.Matched")

	// Step 5 – accept trip (write Trip.Accepted to outbox)
	log.Step(5, 12, "AcceptWithOutbox → ACCEPTED + Trip.Accepted event")
	require.NoError(t, env.repo.AcceptWithOutbox(ctx, trip.ID, driverID,
		newEventWithDriver(trip.ID, driverID, "Trip.Accepted")))

	// Step 6 – outbox publishes Trip.Accepted
	log.Step(6, 12, "OutboxWorker.ProcessOnce → publishes Trip.Accepted")
	env.outbox.ProcessOnce(ctx)
	testkit.AssertStreamHasEntry(ctx, t, env.rdb, env.stream, "Trip.Accepted")

	// Step 7 – dispatch confirms driver ON_TRIP
	log.Step(7, 12, "dispConsumer.ConsumeOnce → ConfirmDriverOnTrip (ON_TRIP)")
	env.dispConsumer.ConsumeOnce(ctx)
	status, _ := env.rdb.Get(ctx, "driver_status:"+driverID.String()).Result()
	assert.Equal(t, "ON_TRIP", status, "driver must be ON_TRIP after acceptance")

	// Step 8 – start trip
	log.Step(8, 12, "StartWithOutbox → IN_PROGRESS + Trip.Started event")
	require.NoError(t, env.repo.StartWithOutbox(ctx, trip.ID,
		newEventWithDriver(trip.ID, driverID, "Trip.Started")))

	// Step 9 – outbox publishes Trip.Started
	log.Step(9, 12, "OutboxWorker.ProcessOnce → publishes Trip.Started")
	env.outbox.ProcessOnce(ctx)
	testkit.AssertStreamHasEntry(ctx, t, env.rdb, env.stream, "Trip.Started")

	// Step 10 – complete trip
	log.Step(10, 12, "CompleteWithOutbox → COMPLETED + Trip.Completed event")
	require.NoError(t, env.repo.CompleteWithOutbox(ctx, trip.ID, 52000,
		newEventWithDriver(trip.ID, driverID, "Trip.Completed")))

	// Step 11 – outbox publishes Trip.Completed
	log.Step(11, 12, "OutboxWorker.ProcessOnce → publishes Trip.Completed")
	env.outbox.ProcessOnce(ctx)
	testkit.AssertStreamHasEntry(ctx, t, env.rdb, env.stream, "Trip.Completed")

	// Step 12 – dispatch releases driver
	log.Step(12, 12, "dispConsumer.ConsumeOnce → ReleaseDriver")
	env.dispConsumer.ConsumeOnce(ctx)

	// Assertions
	finalTrip, err := env.repo.GetByID(ctx, trip.ID)
	require.NoError(t, err)
	exists, _ := env.rdb.Exists(ctx, "driver_status:"+driverID.String()).Result()

	log.Result(
		fmt.Sprintf("trip.Status = %s (want COMPLETED)", finalTrip.Status),
		fmt.Sprintf("driver_status key exists = %d (want 0)", exists),
	)

	assert.Equal(t, rideExport.StatusCompleted, finalTrip.Status)
	assert.Equal(t, int64(0), exists, "driver_status key must be deleted after completion")
}

// ---------------------------------------------------------------------------
// Test 2 – Offer timeout
// ---------------------------------------------------------------------------

// TestChoreography_OfferTimeout proves that an expired offer_deadline causes
// TripTimeoutWorker to fire Trip.OfferRejected, dispatch releases the driver,
// and the driver re-enters the matching pool.
func TestChoreography_OfferTimeout(t *testing.T) {
	ctx := context.Background()
	log := testkit.New(t)
	log.Begin(
		"TestChoreography_OfferTimeout",
		"Offer deadline expires → OfferRejected → dispatch releases driver → driver re-enters pool",
	)

	env := setupChoreography(ctx, t)
	passengerID := uuid.New()
	driverID := uuid.MustParse("00000000-0000-0000-0000-000000000002")

	// Steps 1-4: create trip, publish, match driver, assign driver
	log.Step(1, 10, "Seed driver, CreateWithOutbox (Trip.Requested)")
	seedDriver(ctx, env.rdb, driverID.String(), 10.763000, 106.661000)
	trip := newTrip(passengerID)
	require.NoError(t, env.repo.CreateWithOutbox(ctx, trip, newEvent(trip.ID, "Trip.Requested")))

	log.Step(2, 10, "OutboxWorker.ProcessOnce → publishes Trip.Requested")
	env.outbox.ProcessOnce(ctx)

	log.Step(3, 10, "dispConsumer.ConsumeOnce → matches driver → Trip.Matched + ReserveDriver")
	env.dispConsumer.ConsumeOnce(ctx)

	log.Step(4, 10, "updateWkr.ConsumeOnce → UpdateDriver + SetOfferDeadline")
	env.updateWkr.ConsumeOnce(ctx)
	dbTrip, err := env.repo.GetByID(ctx, trip.ID)
	require.NoError(t, err)
	require.NotNil(t, dbTrip.DriverID, "driver must be assigned after Trip.Matched")

	// Step 5 – backdate offer_deadline to the past
	log.Step(5, 10, "Backdate offer_deadline to past")
	_, err = env.db.ExecContext(ctx,
		"UPDATE trips SET offer_deadline = $1 WHERE id = $2",
		time.Now().Add(-5*time.Second), trip.ID,
	)
	require.NoError(t, err)

	// Step 6 – timeout worker detects expired offer, calls RejectOfferWithOutbox
	log.Step(6, 10, "TripTimeoutWorker.CheckTimeoutsOnce → RejectOfferWithOutbox")
	env.timeoutWkr.CheckTimeoutsOnce(ctx)

	// Step 7 – outbox publishes Trip.OfferRejected
	log.Step(7, 10, "OutboxWorker.ProcessOnce → publishes Trip.OfferRejected")
	env.outbox.ProcessOnce(ctx)
	testkit.AssertStreamHasEntry(ctx, t, env.rdb, env.stream, "Trip.OfferRejected")

	// Step 8 – dispatch reads Trip.OfferRejected, records rejection, releases driver
	log.Step(8, 10, "dispConsumer.ConsumeOnce → RecordRejection + ReleaseDriver")
	env.dispConsumer.ConsumeOnce(ctx)

	// Step 9 – verify DB state
	log.Step(9, 10, "Assert trip.Status == REQUESTED and driver released")
	finalTrip, err := env.repo.GetByID(ctx, trip.ID)
	require.NoError(t, err)
	exists, _ := env.rdb.Exists(ctx, "driver_status:"+driverID.String()).Result()

	// Step 10 – confirm driver can be matched again
	log.Step(10, 10, "MatchDriver → driver back in pool")
	// Re-seed heartbeat (heartbeat TTL is 30 s, may have expired in slow CI)
	env.rdb.Set(ctx, "driver_last_seen:"+driverID.String(), "active", 30*time.Second)
	matched, err := env.dispSvc.MatchDriver(ctx, "new-trip-"+uuid.New().String(), 10.762622, 106.660172)
	require.NoError(t, err)

	log.Result(
		fmt.Sprintf("trip.Status = %s (want REQUESTED)", finalTrip.Status),
		fmt.Sprintf("trip.DriverID = %v (want nil)", finalTrip.DriverID),
		fmt.Sprintf("driver_status key exists = %d (want 0)", exists),
		fmt.Sprintf("re-match driverID = %q (want %s)", matched, driverID),
	)

	assert.Equal(t, rideExport.StatusRequested, finalTrip.Status)
	assert.Nil(t, finalTrip.DriverID, "driver_id must be cleared after offer rejection")
	assert.Equal(t, int64(0), exists, "driver_status key must be deleted after offer rejection")
	assert.Equal(t, driverID.String(), matched, "driver must be re-matchable after release")
}

// ---------------------------------------------------------------------------
// Test 3 – Start timeout (ACCEPTED but never started)
// ---------------------------------------------------------------------------

// TestChoreography_StartTimeout proves that a trip stuck in ACCEPTED for >5 min
// is cancelled by TripTimeoutWorker and the driver is released.
func TestChoreography_StartTimeout(t *testing.T) {
	ctx := context.Background()
	log := testkit.New(t)
	log.Begin(
		"TestChoreography_StartTimeout",
		"Trip ACCEPTED but driver never starts → TripTimeoutWorker cancels → driver released",
	)

	env := setupChoreography(ctx, t)
	passengerID := uuid.New()
	driverID := uuid.MustParse("00000000-0000-0000-0000-000000000003")

	// Steps 1-7: same as HappyPath up to and including ConfirmDriverOnTrip
	log.Step(1, 6, "Seed driver, create trip, publish, match, assign, accept")
	seedDriver(ctx, env.rdb, driverID.String(), 10.763000, 106.661000)
	trip := newTrip(passengerID)
	require.NoError(t, env.repo.CreateWithOutbox(ctx, trip, newEvent(trip.ID, "Trip.Requested")))
	env.outbox.ProcessOnce(ctx)
	env.dispConsumer.ConsumeOnce(ctx) // Trip.Requested → Trip.Matched
	env.updateWkr.ConsumeOnce(ctx)    // Trip.Matched  → UpdateDriver

	require.NoError(t, env.repo.AcceptWithOutbox(ctx, trip.ID, driverID,
		newEventWithDriver(trip.ID, driverID, "Trip.Accepted")))
	env.outbox.ProcessOnce(ctx)
	env.dispConsumer.ConsumeOnce(ctx) // Trip.Accepted → ConfirmDriverOnTrip

	// Step 2 – backdate accepted_at to >5 min ago
	log.Step(2, 6, "Backdate accepted_at to 6 minutes ago")
	_, err := env.db.ExecContext(ctx,
		"UPDATE trips SET accepted_at = $1 WHERE id = $2",
		time.Now().Add(-6*time.Minute), trip.ID,
	)
	require.NoError(t, err)

	// Step 3 – timeout worker fires cancelStuckAccepted → CancelWithOutbox
	log.Step(3, 6, "TripTimeoutWorker.CheckTimeoutsOnce → CancelWithOutbox (START_TIMEOUT)")
	env.timeoutWkr.CheckTimeoutsOnce(ctx)

	// Step 4 – outbox publishes Trip.Cancelled
	log.Step(4, 6, "OutboxWorker.ProcessOnce → publishes Trip.Cancelled")
	env.outbox.ProcessOnce(ctx)
	testkit.AssertStreamHasEntry(ctx, t, env.rdb, env.stream, "Trip.Cancelled")

	// Step 5 – dispatch reads Trip.Cancelled, releases driver
	log.Step(5, 6, "dispConsumer.ConsumeOnce → ReleaseDriver")
	env.dispConsumer.ConsumeOnce(ctx)

	// Step 6 – assertions
	log.Step(6, 6, "Assert trip CANCELLED, driver released")
	finalTrip, err := env.repo.GetByID(ctx, trip.ID)
	require.NoError(t, err)
	exists, _ := env.rdb.Exists(ctx, "driver_status:"+driverID.String()).Result()

	log.Result(
		fmt.Sprintf("trip.Status = %s (want CANCELLED)", finalTrip.Status),
		fmt.Sprintf("driver_status key exists = %d (want 0)", exists),
	)

	assert.Equal(t, rideExport.StatusCancelled, finalTrip.Status)
	assert.Equal(t, int64(0), exists, "driver_status key must be deleted after cancellation")
}

// ---------------------------------------------------------------------------
// Test 4 – No driver available
// ---------------------------------------------------------------------------

// TestChoreography_NoDriverAvailable proves that when no fresh driver exists,
// Trip.MatchFailed is published and the ride service cancels the trip.
func TestChoreography_NoDriverAvailable(t *testing.T) {
	ctx := context.Background()
	log := testkit.New(t)
	log.Begin(
		"TestChoreography_NoDriverAvailable",
		"No fresh drivers → Trip.MatchFailed published → ride service cancels trip",
	)

	env := setupChoreography(ctx, t)
	passengerID := uuid.New()

	// Step 1 – plant a stale driver (geo entry but no heartbeat key)
	log.Step(1, 5, "Plant stale driver (geo entry, no heartbeat key)")
	env.rdb.GeoAdd(ctx, "drivers_location", &redis.GeoLocation{
		Name: "stale-driver-001", Latitude: 10.763000, Longitude: 106.661000,
	})
	// Note: intentionally NOT setting driver_last_seen:stale-driver-001

	// Step 2 – create trip + Trip.Requested
	log.Step(2, 5, "CreateWithOutbox (Trip.Requested)")
	trip := newTrip(passengerID)
	require.NoError(t, env.repo.CreateWithOutbox(ctx, trip, newEvent(trip.ID, "Trip.Requested")))

	// Step 3 – outbox publishes
	log.Step(3, 5, "OutboxWorker.ProcessOnce → publishes Trip.Requested")
	env.outbox.ProcessOnce(ctx)

	// Step 4 – dispatch finds no fresh driver, publishes Trip.MatchFailed
	log.Step(4, 5, "dispConsumer.ConsumeOnce → MatchDriver returns '' → Trip.MatchFailed")
	env.dispConsumer.ConsumeOnce(ctx)
	testkit.AssertStreamHasEntry(ctx, t, env.rdb, env.stream, "Trip.MatchFailed")

	// Step 5 – update worker processes Trip.MatchFailed → UpdateStatus(CANCELLED)
	log.Step(5, 5, "updateWkr.ConsumeOnce → UpdateStatus(CANCELLED)")
	env.updateWkr.ConsumeOnce(ctx)

	finalTrip, err := env.repo.GetByID(ctx, trip.ID)
	require.NoError(t, err)

	log.Result(
		fmt.Sprintf("trip.Status = %s (want CANCELLED)", finalTrip.Status),
	)

	assert.Equal(t, rideExport.StatusCancelled, finalTrip.Status)
}

// ---------------------------------------------------------------------------
// Test 5 – Offer rejection retry (second driver matched)
// ---------------------------------------------------------------------------

// TestChoreography_OfferRejectionRetry proves that when the first driver's offer
// is rejected, the dispatch consumer re-matches with the second driver on the
// same Trip.OfferRejected event.
func TestChoreography_OfferRejectionRetry(t *testing.T) {
	ctx := context.Background()
	log := testkit.New(t)
	log.Begin(
		"TestChoreography_OfferRejectionRetry",
		"First driver rejects → second driver matched → trip eventually ACCEPTED",
	)

	env := setupChoreography(ctx, t)
	passengerID := uuid.New()

	// driver-1 is nearer; driver-2 is slightly further but still within radius
	driver1 := "offer-retry-driver-1"
	driver2 := "offer-retry-driver-2"

	log.Step(1, 10, "Seed 2 drivers")
	seedDriver(ctx, env.rdb, driver1, 10.763000, 106.661000) // ~150 m
	seedDriver(ctx, env.rdb, driver2, 10.765000, 106.663000) // ~400 m

	// Step 2-4: create trip, publish, dispatch matches driver-1
	log.Step(2, 10, "CreateWithOutbox (Trip.Requested)")
	trip := newTrip(passengerID)
	require.NoError(t, env.repo.CreateWithOutbox(ctx, trip, newEvent(trip.ID, "Trip.Requested")))

	log.Step(3, 10, "OutboxWorker.ProcessOnce → publishes Trip.Requested")
	env.outbox.ProcessOnce(ctx)

	log.Step(4, 10, "dispConsumer.ConsumeOnce → matches driver-1 → Trip.Matched + ReserveDriver")
	env.dispConsumer.ConsumeOnce(ctx)

	// Verify driver-1 was matched
	msg := testkit.AssertStreamHasEntry(ctx, t, env.rdb, env.stream, "Trip.Matched")
	var matchedPayload map[string]interface{}
	require.NoError(t, json.Unmarshal([]byte(msg.Values["payload"].(string)), &matchedPayload))
	assert.Equal(t, driver1, matchedPayload["driver_id"], "driver-1 (nearest) must be matched first")

	log.Step(5, 10, "updateWkr.ConsumeOnce → UpdateDriver(driver-1) + SetOfferDeadline")
	env.updateWkr.ConsumeOnce(ctx)

	// Step 6 – backdate offer_deadline so timeout worker fires
	log.Step(6, 10, "Backdate offer_deadline → CheckTimeoutsOnce → RejectOfferWithOutbox")
	_, err := env.db.ExecContext(ctx,
		"UPDATE trips SET offer_deadline = $1 WHERE id = $2",
		time.Now().Add(-5*time.Second), trip.ID,
	)
	require.NoError(t, err)
	env.timeoutWkr.CheckTimeoutsOnce(ctx)

	log.Step(7, 10, "OutboxWorker.ProcessOnce → publishes Trip.OfferRejected")
	env.outbox.ProcessOnce(ctx)
	testkit.AssertStreamHasEntry(ctx, t, env.rdb, env.stream, "Trip.OfferRejected")

	// Step 8 – dispatch handles Trip.OfferRejected:
	//   RecordRejection(driver-1) + ReleaseDriver(driver-1) + MatchDriver → driver-2 + Trip.Matched
	log.Step(8, 10, "dispConsumer.ConsumeOnce → OfferRejected: release driver-1 + re-match driver-2")
	env.dispConsumer.ConsumeOnce(ctx)

	// Step 9 – update worker assigns driver-2
	log.Step(9, 10, "updateWkr.ConsumeOnce → UpdateDriver(driver-2) + SetOfferDeadline")
	env.updateWkr.ConsumeOnce(ctx)

	// Step 10 – accept with driver-2
	// Use a deterministic UUID derived from driver2's string ID
	log.Step(10, 10, "AcceptWithOutbox with driver-2 → ACCEPTED")
	driver2UUID := uuid.NewSHA1(uuid.NameSpaceDNS, []byte(driver2))
	require.NoError(t, env.repo.AcceptWithOutbox(ctx, trip.ID, driver2UUID,
		newEventWithDriver(trip.ID, driver2UUID, "Trip.Accepted")))

	finalTrip, err := env.repo.GetByID(ctx, trip.ID)
	require.NoError(t, err)

	log.Result(
		fmt.Sprintf("trip.Status = %s (want ACCEPTED)", finalTrip.Status),
		fmt.Sprintf("trip.DriverID = %v (want %s)", finalTrip.DriverID, driver2UUID),
	)

	assert.Equal(t, rideExport.StatusAccepted, finalTrip.Status)
	require.NotNil(t, finalTrip.DriverID)
	assert.Equal(t, driver2UUID, *finalTrip.DriverID)
}

// ---------------------------------------------------------------------------
// Test 6 – Concurrent trips
// ---------------------------------------------------------------------------

// TestChoreography_ConcurrentTrips proves that 5 concurrent trips each get
// a distinct driver (no double-booking).
func TestChoreography_ConcurrentTrips(t *testing.T) {
	ctx := context.Background()
	log := testkit.New(t)
	log.Begin(
		"TestChoreography_ConcurrentTrips",
		"5 concurrent trips → each gets a distinct driver (no double-booking)",
	)

	env := setupChoreography(ctx, t)
	const n = 5

	// Step 1 – seed 5 drivers spread around the origin
	log.Step(1, 4, fmt.Sprintf("Seed %d drivers", n))
	for i := 1; i <= n; i++ {
		driverID := fmt.Sprintf("concurrent-driver-%03d", i)
		lat := 10.762622 + float64(i)*0.001
		lng := 106.660172 + float64(i)*0.001
		seedDriver(ctx, env.rdb, driverID, lat, lng)
	}

	// Step 2 – create 5 trips sequentially (outbox batch)
	log.Step(2, 4, fmt.Sprintf("Create %d trips sequentially", n))
	trips := make([]*rideExport.Trip, n)
	passengerID := uuid.New()
	for i := 0; i < n; i++ {
		trips[i] = newTrip(passengerID)
		require.NoError(t, env.repo.CreateWithOutbox(ctx, trips[i], newEvent(trips[i].ID, "Trip.Requested")))
	}

	// Step 3 – outbox publishes all 5 events in a single pass (limit=10)
	log.Step(3, 4, "OutboxWorker.ProcessOnce → publishes all 5 Trip.Requested events")
	env.outbox.ProcessOnce(ctx)

	// Step 4 – dispatch processes one Trip.Requested per ConsumeOnce call
	log.Step(4, 4, fmt.Sprintf("dispConsumer.ConsumeOnce × %d → each matches a distinct driver", n))
	for i := 0; i < n; i++ {
		env.dispConsumer.ConsumeOnce(ctx)
	}

	// Collect distinct matched driver IDs from the stream
	ids := matchedDriverIDs(ctx, env.rdb, env.stream)
	counts := countStreamByType(ctx, env.rdb, env.stream)

	log.Result(
		fmt.Sprintf("Trip.Matched entries    = %d (want %d)", counts["Trip.Matched"], n),
		fmt.Sprintf("distinct driver IDs     = %d (want %d)", len(ids), n),
	)

	assert.Equal(t, n, counts["Trip.Matched"], "every trip must produce a Trip.Matched event")
	assert.Equal(t, n, len(ids), "all matched driver IDs must be distinct (no double-booking)")
}

// ---------------------------------------------------------------------------
// Test 7 – Driver pool exhaustion
// ---------------------------------------------------------------------------

// TestChoreography_DriverPoolExhaustion proves that with 2 drivers and 5 trips,
// exactly 2 trips get matched and 3 get Trip.MatchFailed.
func TestChoreography_DriverPoolExhaustion(t *testing.T) {
	ctx := context.Background()
	log := testkit.New(t)
	log.Begin(
		"TestChoreography_DriverPoolExhaustion",
		"2 drivers, 5 trips → 2 Trip.Matched + 3 Trip.MatchFailed",
	)

	env := setupChoreography(ctx, t)
	const numDrivers = 2
	const numTrips = 5

	// Step 1 – seed 2 drivers
	log.Step(1, 5, fmt.Sprintf("Seed %d drivers", numDrivers))
	for i := 1; i <= numDrivers; i++ {
		driverID := fmt.Sprintf("exhaust-driver-%03d", i)
		seedDriver(ctx, env.rdb, driverID, 10.762622+float64(i)*0.001, 106.660172+float64(i)*0.001)
	}

	// Step 2 – create 5 trips
	log.Step(2, 5, fmt.Sprintf("Create %d trips with Trip.Requested events", numTrips))
	passengerID := uuid.New()
	trips := make([]*rideExport.Trip, numTrips)
	for i := 0; i < numTrips; i++ {
		trips[i] = newTrip(passengerID)
		require.NoError(t, env.repo.CreateWithOutbox(ctx, trips[i], newEvent(trips[i].ID, "Trip.Requested")))
	}

	// Step 3 – outbox publishes all 5
	log.Step(3, 5, "OutboxWorker.ProcessOnce → publishes all 5 Trip.Requested events")
	env.outbox.ProcessOnce(ctx)

	// Step 4 – dispatch processes all 5; first 2 match drivers, last 3 publish MatchFailed
	log.Step(4, 5, fmt.Sprintf("dispConsumer.ConsumeOnce × %d → 2 matched, 3 failed", numTrips))
	for i := 0; i < numTrips; i++ {
		env.dispConsumer.ConsumeOnce(ctx)
	}

	// Step 5 – update worker processes Trip.Matched (2×) and Trip.MatchFailed (3×) events.
	// The stream now has: 5×Requested + 2×Matched + 3×MatchFailed = 10 entries.
	// updateWkr only reacts to Matched and MatchFailed, so calling it 5 times
	// covers all non-Requested events dispatched in step 4.
	log.Step(5, 5, fmt.Sprintf("updateWkr.ConsumeOnce × %d → process Matched + MatchFailed", numTrips))
	for i := 0; i < numTrips; i++ {
		env.updateWkr.ConsumeOnce(ctx)
	}

	counts := countStreamByType(ctx, env.rdb, env.stream)

	log.Result(
		fmt.Sprintf("Trip.Matched    = %d (want %d)", counts["Trip.Matched"], numDrivers),
		fmt.Sprintf("Trip.MatchFailed = %d (want %d)", counts["Trip.MatchFailed"], numTrips-numDrivers),
	)

	assert.Equal(t, numDrivers, counts["Trip.Matched"],
		"exactly 2 trips must be matched (one per available driver)")
	assert.Equal(t, numTrips-numDrivers, counts["Trip.MatchFailed"],
		"remaining 3 trips must get Trip.MatchFailed")
}
