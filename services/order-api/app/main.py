import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .database import Base, engine, get_db
from .invoice import build_invoice_pdf
from .models import Order, OrderItem, OrderStatus
from .schemas import CheckoutRequest, OrderResponse
from .security import authenticated_user
from .storage import object_storage_client

settings = get_settings()
logger = logging.getLogger(settings.service_name)
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"%(name)s","message":"%(message)s"}',
)
REQUESTS = Counter("http_requests_total", "HTTP requests", ["service", "method", "path", "status"])
LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["service", "path"])


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.auto_create_schema:
        Base.metadata.create_all(bind=engine)
    yield
    engine.dispose()


app = FastAPI(
    title="Phase 1 Order Service",
    version="1.0.0",
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def request_controls(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    with LATENCY.labels(settings.service_name, request.url.path).time():
        response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    REQUESTS.labels(settings.service_name, request.method, request.url.path, response.status_code).inc()
    logger.info("request_id=%s method=%s path=%s status=%s", request_id, request.method, request.url.path, response.status_code)
    return response


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": {"message": exc.detail, "status": exc.status_code}})


@app.get("/health/live", include_in_schema=False)
def live() -> dict[str, str]:
    return {"status": "ok", "service": settings.service_name}


@app.get("/health/ready", include_in_schema=False)
def ready(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
        response = httpx.get(f"{settings.backend_url}/health/live", timeout=2.0)
        response.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="required dependency unavailable") from exc
    return {"status": "ready", "service": settings.service_name}


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def reserve_inventory(payload: CheckoutRequest) -> dict:
    try:
        response = httpx.post(
            f"{settings.backend_url}/internal/products/reserve",
            json={"items": [item.model_dump() for item in payload.items]},
            headers={"X-Internal-Token": settings.internal_service_token},
            timeout=5.0,
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="catalog service unavailable") from exc
    if response.status_code == 409:
        raise HTTPException(status_code=409, detail="one or more products are unavailable")
    if response.is_error:
        raise HTTPException(status_code=502, detail="catalog reservation failed")
    return response.json()


def release_inventory(reservation_id: str, items: list[dict]) -> None:
    try:
        response = httpx.post(
            f"{settings.backend_url}/internal/products/release",
            json={"reservation_id": reservation_id, "items": items},
            headers={"X-Internal-Token": settings.internal_service_token},
            timeout=5.0,
        )
        response.raise_for_status()
    except Exception:
        logger.exception("inventory compensation failed reservation_id=%s", reservation_id)


def upload_invoice(order: Order) -> str | None:
    now = datetime.now(timezone.utc)
    key = f"invoices/{now.year:04d}/{now.month:02d}/{order.id}.pdf"
    lines = [f"{item.quantity} x {item.product_name} @ {item.unit_price:.2f}" for item in order.items]
    try:
        object_storage_client().put_object(
            Bucket=settings.object_storage_bucket,
            Key=key,
            Body=build_invoice_pdf(order.id, order.total_amount, order.currency, lines),
            ContentType="application/pdf",
            ServerSideEncryption="AES256",
        )
    except Exception:
        logger.exception("invoice upload failed order_id=%s", order.id)
        return None
    return key


@app.post("/api/v1/orders", response_model=OrderResponse, status_code=201)
def checkout(
    payload: CheckoutRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    user: dict[str, str] = Depends(authenticated_user),
    db: Session = Depends(get_db),
) -> Order:
    existing = db.scalar(select(Order).where(Order.user_id == user["id"], Order.idempotency_key == idempotency_key))
    if existing:
        return existing

    reservation = reserve_inventory(payload)
    products = reservation["products"]
    total = sum(Decimal(str(item["unit_price"])) * item["quantity"] for item in products)
    order = Order(
        user_id=user["id"],
        idempotency_key=idempotency_key,
        reservation_id=reservation["reservation_id"],
        total_amount=total,
        currency=payload.currency,
        shipping_address=payload.shipping_address,
    )
    order.items = [
        OrderItem(
            product_id=item["product_id"],
            sku=item["sku"],
            product_name=item["name"],
            quantity=item["quantity"],
            unit_price=Decimal(str(item["unit_price"])),
        )
        for item in products
    ]
    db.add(order)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        release_inventory(reservation["reservation_id"], [item.model_dump() for item in payload.items])
        existing = db.scalar(select(Order).where(Order.user_id == user["id"], Order.idempotency_key == idempotency_key))
        if existing:
            return existing
        raise
    except Exception:
        db.rollback()
        release_inventory(reservation["reservation_id"], [item.model_dump() for item in payload.items])
        raise
    db.refresh(order)

    invoice_key = upload_invoice(order)
    if invoice_key:
        order.invoice_key = invoice_key
        db.commit()
        db.refresh(order)
    return order


@app.get("/api/v1/orders", response_model=list[OrderResponse])
def list_orders(user: dict[str, str] = Depends(authenticated_user), db: Session = Depends(get_db)) -> list[Order]:
    return list(db.scalars(select(Order).where(Order.user_id == user["id"]).order_by(Order.created_at.desc()).limit(100)).all())


@app.get("/api/v1/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: str, user: dict[str, str] = Depends(authenticated_user), db: Session = Depends(get_db)) -> Order:
    order = db.scalar(select(Order).where(Order.id == order_id, Order.user_id == user["id"]))
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return order


@app.post("/api/v1/orders/{order_id}/cancel", response_model=OrderResponse)
def cancel_order(order_id: str, user: dict[str, str] = Depends(authenticated_user), db: Session = Depends(get_db)) -> Order:
    order = db.scalar(select(Order).where(Order.id == order_id, Order.user_id == user["id"]).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if order.status == OrderStatus.cancelled:
        release_inventory(
            order.reservation_id,
            [{"product_id": item.product_id, "quantity": item.quantity} for item in order.items],
        )
        return order
    order.status = OrderStatus.cancelled
    db.commit()
    release_inventory(
        order.reservation_id,
        [{"product_id": item.product_id, "quantity": item.quantity} for item in order.items],
    )
    db.refresh(order)
    return order
