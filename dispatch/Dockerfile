# Production Dockerfile for Dispatch Service
ARG BASE_IMAGE_PREFIX=""
FROM ${BASE_IMAGE_PREFIX}golang:alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o main .

FROM ${BASE_IMAGE_PREFIX}alpine:latest
RUN apk --no-cache add ca-certificates
WORKDIR /app
COPY --from=builder /app/main .
EXPOSE 8080
CMD ["./main"]
