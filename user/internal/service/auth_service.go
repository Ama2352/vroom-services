package service

import (
	"context"
	"errors"
	"time"
	"vroom-mvp/user/internal/domain"
	"vroom-mvp/user/internal/repository"
	"vroom-mvp/user/internal/util"

	"github.com/google/uuid"
)

type AuthService struct {
	repo       repository.UserRepository
	jwtManager *util.JWTManager
}

func NewAuthService(repo repository.UserRepository, jwtManager *util.JWTManager) *AuthService {
	return &AuthService{
		repo:       repo,
		jwtManager: jwtManager,
	}
}

func (s *AuthService) Register(ctx context.Context, req domain.RegisterRequest) (*domain.AuthResponse, error) {
	email, err := domain.NewEmail(req.Email)
	if err != nil {
		return nil, err
	}

	phone, err := domain.NewPhoneNumber(req.PhoneNumber, "+1") // Default country code for now
	if err != nil {
		return nil, err
	}

	// Check if user already exists
	existing, err := s.repo.GetByEmail(ctx, email)
	if err != nil {
		return nil, err
	}
	if existing != nil {
		return nil, errors.New("user already exists")
	}

	// Hash password
	passwordHash, err := util.HashPassword(req.Password)
	if err != nil {
		return nil, err
	}

	// Create user domain object
	user := &domain.User{
		ID:           uuid.New(),
		Email:        email,
		PhoneNumber:  phone,
		PasswordHash: passwordHash,
		Name:         req.Name,
		Role:         req.Role,
		CreatedAt:    time.Now(),
	}

	// Create outbox event
	event := &repository.OutboxEvent{
		ID:            uuid.New(),
		AggregateType: "User",
		AggregateID:   user.ID,
		EventType:     "User.Created",
		Payload:       user,
	}

	// Save to DB (transactional)
	if err := s.repo.CreateWithOutbox(ctx, user, event); err != nil {
		return nil, err
	}

	// Generate JWT
	token, err := s.jwtManager.Generate(user.ID, string(user.Role))
	if err != nil {
		return nil, err
	}

	return &domain.AuthResponse{
		UserID:      user.ID,
		AccessToken: token,
		ExpiresIn:   3600, // 1 hour
	}, nil
}

func (s *AuthService) Login(ctx context.Context, req domain.LoginRequest) (*domain.AuthResponse, error) {
	email, err := domain.NewEmail(req.Email)
	if err != nil {
		return nil, err
	}

	user, err := s.repo.GetByEmail(ctx, email)
	if err != nil {
		return nil, err
	}
	if user == nil {
		return nil, errors.New("invalid credentials")
	}

	// Verify password
	if !util.CheckPasswordHash(req.Password, user.PasswordHash) {
		return nil, errors.New("invalid credentials")
	}

	// Generate JWT
	token, err := s.jwtManager.Generate(user.ID, string(user.Role))
	if err != nil {
		return nil, err
	}

	return &domain.AuthResponse{
		UserID:      user.ID,
		AccessToken: token,
		ExpiresIn:   3600,
	}, nil
}

