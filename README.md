# Vroom Services: AI-Enhanced Microservices

This repository contains the Go-based microservices and CI/CD logic for the **Vroom** ride-hailing platform.

## 🏗️ Architecture

The platform follows a distributed architecture designed for reliability and eventual consistency.

| Service          | Responsibility          | Stack                         |
| :--------------- | :---------------------- | :---------------------------- |
| **User**         | Identity & JWT (RS256)  | Go, PostgreSQL, SQLC          |
| **Ride**         | Booking & State Machine | Go, PostgreSQL, Redis Streams |
| **Dispatch**     | Driver Matching (Geo)   | Go, Redis GeoSearch           |
| **Notification** | Slack & User Alerts     | Go, Redis Streams             |
| **AI Reporter**  | Health Analysis         | Go, Gemini Flash, Prometheus  |

## 🌟 Key Technical Patterns

### Transactional Outbox Pattern

Ensures that domain events (e.g., `TripCreated`) are never lost. The event is saved to the database in the same transaction as the domain object and then published to **Redis Streams** by a background worker.

### Async Event-Driven Communication

Services communicate asynchronously via **Redis Streams** using Consumer Groups, allowing for horizontal scalability and fault tolerance.

### AI-Driven Observability

The **AI Reporter** service acts as a "Virtual SRE." It periodically queries Prometheus metrics and uses **Gemini Flash** to perform root-cause analysis, sending natural-language health reports to Slack.

## 🎡 CI/CD Pipeline (GitLab CI)

The `.gitlab-ci.yml` defines a robust DevSecOps pipeline:

1.  **Test**: Unit and integration testing.
2.  **Scan**: Static analysis with **SonarQube** and security scanning with **Trivy**.
3.  **Build**: Multi-arch Docker builds.
4.  **Publish**: Pushing to Docker Hub/ECR with automated versioning.
5.  **Deploy**: Triggering [vroom-gitops](https://github.com/Ama2352/vroom-gitops) updates.

## 🔗 Related Repositories

- **[vroom-gitops](https://github.com/Ama2352/vroom-gitops)**: The GitOps promotion engine.
- **[vroom-infra](https://github.com/Ama2352/vroom-infra)**: Cluster provisioning logic.
