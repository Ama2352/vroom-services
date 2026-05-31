package handler

import (
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

const CorrelationIDHeader = "X-Correlation-ID"

func CorrelationMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		corID := c.GetHeader(CorrelationIDHeader)
		if corID == "" {
			corID = uuid.New().String()
		}
		c.Set("CorrelationID", corID)
		c.Header(CorrelationIDHeader, corID)
		c.Next()
	}
}

func GetCorrelationID(c *gin.Context) string {
	id, exists := c.Get("CorrelationID")
	if !exists {
		return ""
	}
	return id.(string)
}
