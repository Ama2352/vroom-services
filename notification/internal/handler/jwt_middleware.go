package handler

import (
	"crypto/rsa"
	"crypto/x509"
	"encoding/pem"
	"net/http"
	"os"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
)

// JWTMiddleware validates RS256 Bearer tokens issued by the User service.
// Reads JWT_PUBLIC_KEY_PEM from env. If unset, all requests pass through (dev mode).
func JWTMiddleware() gin.HandlerFunc {
	publicKeyPEM := os.Getenv("JWT_PUBLIC_KEY_PEM")
	if publicKeyPEM == "" {
		return func(c *gin.Context) { c.Next() }
	}

	block, _ := pem.Decode([]byte(publicKeyPEM))
	if block == nil {
		panic("JWT_PUBLIC_KEY_PEM: invalid PEM block")
	}
	pub, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		panic("JWT_PUBLIC_KEY_PEM: " + err.Error())
	}
	rsaPub := pub.(*rsa.PublicKey)

	return func(c *gin.Context) {
		authHeader := c.GetHeader("Authorization")
		if authHeader == "" || !strings.HasPrefix(authHeader, "Bearer ") {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "missing authorization token"})
			return
		}
		tokenStr := strings.TrimPrefix(authHeader, "Bearer ")

		token, err := jwt.Parse(tokenStr, func(t *jwt.Token) (interface{}, error) {
			if _, ok := t.Method.(*jwt.SigningMethodRSA); !ok {
				return nil, jwt.ErrSignatureInvalid
			}
			return rsaPub, nil
		})
		if err != nil || !token.Valid {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "invalid token"})
			return
		}

		claims, ok := token.Claims.(jwt.MapClaims)
		if !ok {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "invalid token claims"})
			return
		}
		c.Set("sub", claims["sub"])
		c.Set("role", claims["role"])
		c.Next()
	}
}
