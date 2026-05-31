package domain

import (
	"errors"
	"regexp"
	"strings"
	"time"

	"github.com/google/uuid"
)

var (
	ErrInvalidEmail       = errors.New("invalid email format")
	ErrInvalidPhoneNumber = errors.New("invalid phone number format")
	ErrUnauthorizedRole   = errors.New("only passengers can be assigned as drivers")
)

type Role string

const (
	RolePassenger Role = "PASSENGER"
	RoleDriver    Role = "DRIVER"
)

type Email struct {
	Address string `json:"address"`
}

func NewEmail(address string) (Email, error) {
	emailRegex := regexp.MustCompile(`^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,4}$`)
	if !emailRegex.MatchString(strings.ToLower(address)) {
		return Email{}, ErrInvalidEmail
	}
	return Email{Address: address}, nil
}

func (e Email) Domain() string {
	parts := strings.Split(e.Address, "@")
	if len(parts) != 2 {
		return ""
	}
	return parts[1]
}

func (e Email) String() string {
	return e.Address
}

type PhoneNumber struct {
	Number      string `json:"number"`
	CountryCode string `json:"country_code"`
	Verified    bool   `json:"verified"`
}

func NewPhoneNumber(number string, countryCode string) (PhoneNumber, error) {
	// Simple validation for example purposes
	if len(number) < 8 {
		return PhoneNumber{}, ErrInvalidPhoneNumber
	}
	return PhoneNumber{
		Number:      number,
		CountryCode: countryCode,
		Verified:    false,
	}, nil
}

type User struct {
	ID           uuid.UUID   `json:"id"`
	Email        Email       `json:"email"`
	PhoneNumber  PhoneNumber `json:"phone_number"`
	PasswordHash string      `json:"-"`
	Name         string      `json:"name"`
	Role         Role        `json:"role"`
	CreatedAt    time.Time   `json:"created_at"`
}

// AssignDriver ensures only passengers can become drivers.
func (u *User) AssignDriver() error {
	if u.Role != RolePassenger {
		return ErrUnauthorizedRole
	}
	u.Role = RoleDriver
	return nil
}

type RegisterRequest struct {
	Email       string `json:"email" binding:"required,email"`
	Password    string `json:"password" binding:"required,min=8"`
	Name        string `json:"name" binding:"required"`
	PhoneNumber string `json:"phone_number" binding:"required"`
	Role        Role   `json:"role" binding:"required,oneof=PASSENGER DRIVER"`
}

type LoginRequest struct {
	Email    string `json:"email" binding:"required,email"`
	Password string `json:"password" binding:"required"`
}

type AuthResponse struct {
	UserID      uuid.UUID `json:"user_id"`
	AccessToken string    `json:"access_token"`
	ExpiresIn   int       `json:"expires_in"`
}
