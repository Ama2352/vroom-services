# Using docker-compose to run migrations
DOCKER_COMPOSE=docker-compose -f ../docker-compose.yml
MIGRATE_CMD=$(DOCKER_COMPOSE) run --rm user-migrate

.PHONY: migrate-up migrate-down migrate-reset

migrate-up:
	$(MIGRATE_CMD) -path=/migrations/ -database="postgres://vroom:vroom_dev@postgres:5432/vroom?sslmode=disable&x-migrations-table=user_migrations" up

migrate-down:
	$(MIGRATE_CMD) -path=/migrations/ -database="postgres://vroom:vroom_dev@postgres:5432/vroom?sslmode=disable&x-migrations-table=user_migrations" down 1

migrate-reset:
	$(MIGRATE_CMD) -path=/migrations/ -database="postgres://vroom:vroom_dev@postgres:5432/vroom?sslmode=disable&x-migrations-table=user_migrations" down 1
	$(MIGRATE_CMD) -path=/migrations/ -database="postgres://vroom:vroom_dev@postgres:5432/vroom?sslmode=disable&x-migrations-table=user_migrations" up
