-- Global DB initialization
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create schemas for each service to achieve logical isolation
CREATE SCHEMA IF NOT EXISTS users;
CREATE SCHEMA IF NOT EXISTS rides;
CREATE SCHEMA IF NOT EXISTS notifications;
CREATE SCHEMA IF NOT EXISTS dispatch;

-- Create service-specific users with schema access
CREATE USER user_svc WITH PASSWORD 'vroom_dev';
GRANT USAGE ON SCHEMA users TO user_svc;
GRANT CREATE ON SCHEMA users TO user_svc;
ALTER DEFAULT PRIVILEGES IN SCHEMA users GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO user_svc;
ALTER USER user_svc SET search_path TO users, public;

CREATE USER ride_svc WITH PASSWORD 'vroom_dev';
GRANT USAGE ON SCHEMA rides TO ride_svc;
GRANT CREATE ON SCHEMA rides TO ride_svc;
ALTER DEFAULT PRIVILEGES IN SCHEMA rides GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ride_svc;
ALTER USER ride_svc SET search_path TO rides, public;

CREATE USER notification_svc WITH PASSWORD 'vroom_dev';
GRANT USAGE ON SCHEMA notifications TO notification_svc;
GRANT CREATE ON SCHEMA notifications TO notification_svc;
ALTER DEFAULT PRIVILEGES IN SCHEMA notifications GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO notification_svc;
ALTER USER notification_svc SET search_path TO notifications, public;

CREATE USER dispatch_svc WITH PASSWORD 'vroom_dev';
GRANT USAGE ON SCHEMA dispatch TO dispatch_svc;
GRANT CREATE ON SCHEMA dispatch TO dispatch_svc;
ALTER DEFAULT PRIVILEGES IN SCHEMA dispatch GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dispatch_svc;
ALTER USER dispatch_svc SET search_path TO dispatch, public;

-- Revoke public schema access from all to enforce isolation
REVOKE ALL ON SCHEMA public FROM PUBLIC;
