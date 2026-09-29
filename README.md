# Phase 1 E-commerce Application

This repository now defaults to the smallest application topology required by the Phase 1 single-VPC scenario:

```text
Internet -> DNS/Anti-DDoS/CFW/WAF/ELB -> Ingress
                                            |-- Frontend
                                            |-- Backend API -> RDS MySQL / DCS Redis / OBS
                                            `-- Order Service -> Backend API / RDS MySQL / OBS
```

The cloud-edge services are managed platform concerns. The code implements the three CCE workloads and their health, security, scaling, storage, and observability integration points.

## Service boundaries

| Workload | Responsibility |
| --- | --- |
| Frontend | Product browsing, account access, cart, and checkout UI; also acts as the local reverse proxy |
| Backend API | Registration/login, JWT identity, product catalog, Redis carts, object uploads, and atomic inventory reservation |
| Order Service | Idempotent checkout, order history, cancellation/compensation, and PDF invoice storage |

MySQL is used locally in place of RDS, Redis in place of DCS, and versioned MinIO in place of OBS. No RabbitMQ, MongoDB, payment, review, recommendation, or notification service is required for the Phase 1 request path. The previous seven-service stack remains available in `docker-compose.legacy.yaml`, with its documentation in `README.legacy.md`.

## Run locally

For a quick local evaluation, the checked-in defaults are usable only on a developer machine:

```bash
docker compose up --build
```

Open <http://localhost:8080>. Two demo products are seeded by default. For any shared environment, create an environment file and replace every secret:

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
```

The only host-facing port is the frontend/gateway. MySQL, Redis, object storage, and internal service endpoints remain on isolated Docker networks.

## Core API flow

Register and sign in:

```bash
curl -sS http://localhost:8080/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"buyer@example.com","password":"StrongPassword123"}'

curl -sS http://localhost:8080/api/v1/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"email":"buyer@example.com","password":"StrongPassword123"}'
```

Use the returned bearer token to manage `/api/v1/cart` and place an order at `/api/v1/orders`. Every checkout must include an `Idempotency-Key` header. Product reservations use a row lock, and a failed order write triggers inventory compensation.

Health and Prometheus-format metrics are exposed internally at `/health/live`, `/health/ready`, and `/metrics`. Container logs are structured for collection by LTS or another centralized log collector.

## Deploy to CCE/Kubernetes

1. Build and push `backend-api`, `order-service`, and `frontend` images.
2. Replace the example registry names and host in `infrastruture/kubernetes/phase1/app.yaml`.
3. Copy `infrastruture/kubernetes/phase1/secret.example.yaml` to a private file, replace every placeholder with RDS, DCS, OBS, and application credentials, then apply it.
4. Run `./deploy.sh`.

The manifest supplies two replicas, resource requests/limits, readiness/liveness probes, HPAs, disruption budgets, TLS ingress, a schema-initialization job, non-root/read-only containers, and ingress network policies. Configure the surrounding cloud services separately as described in the customer guide: private CCE/database/storage subnets, deny-by-default security groups, WAF/CFW/Anti-DDoS, Cloud Eye alarms, LTS/CTS, SMN topics, IAM, and native RDS/OBS/CBS backups.

## Production checklist

- Set TLS 1.2+ at WAF/ELB and replace `shop.example.com`.
- Store secrets in the platform secret manager; never apply the example values.
- Disable demo seeding and local schema creation (the Kubernetes configuration already does).
- Restrict RDS 3306 and DCS 6379 to the CCE security group.
- Keep the OBS bucket private; expose product images through signed URLs or a CDN.
- Configure RDS daily backups, OBS versioning/lifecycle, and restore tests.
- Route application, ingress, WAF, CFW, and RDS logs to LTS without sensitive fields.
- Set Cloud Eye alarms for latency, error rate, CPU, memory, pod health, RDS, DCS, and storage failures; deliver critical alarms through SMN.
