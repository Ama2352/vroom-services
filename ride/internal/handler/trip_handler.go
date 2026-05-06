package handler

import (
	"net/http"
	"vroom-mvp/ride/internal/domain"
	"vroom-mvp/ride/internal/service"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

type TripHandler struct {
	tripService *service.TripService
}

func NewTripHandler(tripService *service.TripService) *TripHandler {
	return &TripHandler{
		tripService: tripService,
	}
}

func (h *TripHandler) RequestRide(c *gin.Context) {
	var req domain.CreateTripRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	// In a real app, we would get this from JWT context
	// For now, we allow passing it in headers or hardcode for MVP testing
	passengerIDStr := c.GetHeader("X-User-ID")
	passengerID, err := uuid.Parse(passengerIDStr)
	if err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "Missing or invalid X-User-ID header"})
		return
	}

	trip, err := h.tripService.RequestTrip(c.Request.Context(), passengerID, req)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusCreated, domain.TripResponse{
		TripID: trip.ID,
		Status: trip.Status,
	})
}

func (h *TripHandler) GetTrip(c *gin.Context) {
	idStr := c.Param("id")
	id, err := uuid.Parse(idStr)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid trip ID"})
		return
	}

	trip, err := h.tripService.GetTrip(c.Request.Context(), id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	if trip == nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "Trip not found"})
		return
	}

	c.JSON(http.StatusOK, trip)
}

func (h *TripHandler) CompleteTrip(c *gin.Context) {
	idStr := c.Param("id")
	id, err := uuid.Parse(idStr)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid trip ID"})
		return
	}

	var req struct {
		FinalPrice float64 `json:"final_price" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	err = h.tripService.CompleteTrip(c.Request.Context(), id, req.FinalPrice)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, gin.H{"status": "COMPLETED", "trip_id": id})
}

func (h *TripHandler) AcceptTrip(c *gin.Context) {
	idStr := c.Param("id")
	id, err := uuid.Parse(idStr)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid trip ID format. Expected UUID, got: " + idStr})
		return
	}

	var req struct {
		DriverID string `json:"driver_id" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request body: " + err.Error()})
		return
	}

	driverID, err := uuid.Parse(req.DriverID)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid driver_id format. Expected UUID, got: " + req.DriverID})
		return
	}

	err = h.tripService.AcceptTrip(c.Request.Context(), id, driverID)

	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, gin.H{"status": "ACCEPTED", "trip_id": id})
}

func (h *TripHandler) StartTrip(c *gin.Context) {
	idStr := c.Param("id")
	id, err := uuid.Parse(idStr)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid trip ID format"})
		return
	}

	err = h.tripService.StartTrip(c.Request.Context(), id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, gin.H{"status": "IN_PROGRESS", "trip_id": id})
}

func (h *TripHandler) RejectOffer(c *gin.Context) {
	idStr := c.Param("id")
	id, err := uuid.Parse(idStr)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid trip ID format"})
		return
	}

	var req struct {
		DriverID string `json:"driver_id" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request body"})
		return
	}

	driverID, err := uuid.Parse(req.DriverID)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid driver_id format"})
		return
	}

	err = h.tripService.RejectTripOffer(c.Request.Context(), id, driverID)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, gin.H{"status": "OFFER_REJECTED", "trip_id": id})
}

func (h *TripHandler) CancelTrip(c *gin.Context) {
	idStr := c.Param("id")
	id, err := uuid.Parse(idStr)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid trip ID format"})
		return
	}

	var req struct {
		Reason string `json:"reason"`
	}
	_ = c.ShouldBindJSON(&req)

	err = h.tripService.CancelTrip(c.Request.Context(), id, req.Reason)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, gin.H{"status": "CANCELLED", "trip_id": id})
}

func (h *TripHandler) Health(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"status": "UP"})
}
