package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"time"
	"vroom-mvp/user/internal/domain"
	"vroom-mvp/user/internal/repository/db"

	"github.com/google/uuid"
)

type PostgresUserRepository struct {
	conn    *sql.DB
	queries *db.Queries
}

func NewPostgresUserRepository(conn *sql.DB) *PostgresUserRepository {
	return &PostgresUserRepository{
		conn:    conn,
		queries: db.New(conn),
	}
}

func (r *PostgresUserRepository) CreateWithOutbox(ctx context.Context, user *domain.User, event *OutboxEvent) error {
	tx, err := r.conn.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	qtx := r.queries.WithTx(tx)

	// 1. Create User
	err = qtx.CreateUser(ctx, db.CreateUserParams{
		ID:            user.ID,
		Email:         user.Email.String(),
		PasswordHash:  user.PasswordHash,
		Name:          sql.NullString{String: user.Name, Valid: user.Name != ""},
		Role:          string(user.Role),
		PhoneNumber:   sql.NullString{String: user.PhoneNumber.Number, Valid: user.PhoneNumber.Number != ""},
		PhoneVerified: sql.NullBool{Bool: user.PhoneNumber.Verified, Valid: true},
		CreatedAt:     sql.NullTime{Time: user.CreatedAt, Valid: !user.CreatedAt.IsZero()},
	})
	if err != nil {
		return err
	}

	// 2. Create Outbox Event
	payload, err := json.Marshal(event.Payload)
	if err != nil {
		return err
	}

	err = qtx.CreateOutboxEvent(ctx, db.CreateOutboxEventParams{
		ID:            event.ID,
		AggregateType: event.AggregateType,
		AggregateID:   event.AggregateID,
		EventType:     event.EventType,
		Payload:       payload,
		Status:        sql.NullString{String: "PENDING", Valid: true},
		CreatedAt:     sql.NullTime{Time: time.Now(), Valid: true},
	})
	if err != nil {
		return err
	}

	return tx.Commit()
}

func (r *PostgresUserRepository) GetByID(ctx context.Context, id uuid.UUID) (*domain.User, error) {
	row, err := r.queries.GetUserByID(ctx, id)
	if err != nil {
		if err == sql.ErrNoRows {
			return nil, nil
		}
		return nil, err
	}

	return toDomainUser(row), nil
}

func (r *PostgresUserRepository) GetByEmail(ctx context.Context, email domain.Email) (*domain.User, error) {
	row, err := r.queries.GetUserByEmail(ctx, email.String())
	if err != nil {
		if err == sql.ErrNoRows {
			return nil, nil
		}
		return nil, err
	}

	return toDomainUser(row), nil
}

func (r *PostgresUserRepository) Update(ctx context.Context, user *domain.User) error {
	return r.queries.UpdateUser(ctx, db.UpdateUserParams{
		Email:         user.Email.String(),
		Name:          sql.NullString{String: user.Name, Valid: user.Name != ""},
		Role:          string(user.Role),
		PhoneNumber:   sql.NullString{String: user.PhoneNumber.Number, Valid: user.PhoneNumber.Number != ""},
		PhoneVerified: sql.NullBool{Bool: user.PhoneNumber.Verified, Valid: true},
		ID:            user.ID,
	})
}

func toDomainUser(u db.User) *domain.User {
	email, _ := domain.NewEmail(u.Email)
	return &domain.User{
		ID:           u.ID,
		Email:        email,
		PhoneNumber:  domain.PhoneNumber{Number: u.PhoneNumber.String, Verified: u.PhoneVerified.Bool},
		PasswordHash: u.PasswordHash,
		Name:         u.Name.String,
		Role:         domain.Role(u.Role),
		CreatedAt:    u.CreatedAt.Time,
	}
}

