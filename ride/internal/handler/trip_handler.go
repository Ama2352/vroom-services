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

func (h *TripHandler) Health(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"status": "UP"})
}
