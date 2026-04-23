package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"vroom-mvp/user/internal/domain"

	"github.com/google/uuid"
)

type PostgresUserRepository struct {
	db *sql.DB
}

func NewPostgresUserRepository(db *sql.DB) *PostgresUserRepository {
	return &PostgresUserRepository{db: db}
}

func (r *PostgresUserRepository) CreateWithOutbox(ctx context.Context, user *domain.User, event *OutboxEvent) error {
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	// 1. Insert User
	userQuery := `INSERT INTO users (id, email, password_hash, name, role, created_at) 
				  VALUES ($1, $2, $3, $4, $5, $6)`
	if _, err := tx.ExecContext(ctx, userQuery, user.ID, user.Email, user.PasswordHash, user.Name, user.Role, user.CreatedAt); err != nil {
		return err
	}

	// 2. Insert Outbox Event
	payload, err := json.Marshal(event.Payload)
	if err != nil {
		return err
	}

	outboxQuery := `INSERT INTO outbox_events (id, aggregate_type, aggregate_id, event_type, payload, status, created_at)
					VALUES ($1, $2, $3, $4, $5, $6, NOW())`
	if _, err := tx.ExecContext(ctx, outboxQuery, event.ID, event.AggregateType, event.AggregateID, event.EventType, payload, "PENDING"); err != nil {
		return err
	}

	return tx.Commit()
}

func (r *PostgresUserRepository) GetByID(ctx context.Context, id uuid.UUID) (*domain.User, error) {
	query := `SELECT id, email, password_hash, name, role, created_at FROM users WHERE id = $1`
	row := r.db.QueryRowContext(ctx, query, id)

	var user domain.User
	err := row.Scan(&user.ID, &user.Email, &user.PasswordHash, &user.Name, &user.Role, &user.CreatedAt)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &user, nil
}

func (r *PostgresUserRepository) GetByEmail(ctx context.Context, email string) (*domain.User, error) {
	query := `SELECT id, email, password_hash, name, role, created_at FROM users WHERE email = $1`
	row := r.db.QueryRowContext(ctx, query, email)

	var user domain.User
	err := row.Scan(&user.ID, &user.Email, &user.PasswordHash, &user.Name, &user.Role, &user.CreatedAt)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &user, nil
}

func (r *PostgresUserRepository) Update(ctx context.Context, user *domain.User) error {
	query := `UPDATE users SET email = $1, name = $2, role = $3 WHERE id = $4`
	_, err := r.db.ExecContext(ctx, query, user.Email, user.Name, user.Role, user.ID)
	return err
}
