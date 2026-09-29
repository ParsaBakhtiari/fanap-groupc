import json
import logging
import sys
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from redis import Redis
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .database import Base, SessionLocal, engine, get_db
from .models import Product, ProductReservation, User
from .schemas import (
    CartItem,
    CartResponse,
    LoginRequest,
    ProductResponse,
    RegisterRequest,
    ReleaseRequest,
    ReserveRequest,
    ReserveResponse,
    ReservedProduct,
    TokenResponse,
    UserResponse,
)
from .security import create_access_token, current_user, hash_password, require_internal_token, verify_password
from .storage import object_storage_client

settings = get_settings()
logger = logging.getLogger(settings.service_name)
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"%(name)s","message":"%(message)s"}',
)
redis_client = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)
REQUESTS = Counter("http_requests_total", "HTTP requests", ["service", "method", "path", "status"])
LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["service", "path"])


def seed_products() -> None:
    with SessionLocal() as db:
        if db.scalar(select(Product.id).limit(1)):
            return
        db.add_all(
            [
                Product(sku="PHASE1-TSHIRT", name="Cloud T-shirt", description="Phase 1 demo product", price=Decimal("24.90"), stock_quantity=100),
                Product(sku="PHASE1-MUG", name="Cloud Mug", description="Phase 1 demo product", price=Decimal("12.50"), stock_quantity=100),
            ]
        )
        db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.auto_create_schema:
        Base.metadata.create_all(bind=engine)
    if settings.seed_demo_data:
        seed_products()
    yield
    engine.dispose()
    redis_client.close()


app = FastAPI(
    title="Phase 1 E-commerce Backend API",
    version="1.0.0",
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
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
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/v1/auth") else "no-cache"
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
        redis_client.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="required dependency unavailable") from exc
    return {"status": "ready", "service": settings.service_name}


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/v1/auth/register", response_model=UserResponse, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> User:
    user = User(email=payload.email.lower(), password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="account already exists") from exc
    db.refresh(user)
    return user


@app.post("/api/v1/auth/token", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not verify_password(payload.password, user.password_hash) or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    return TokenResponse(
        access_token=create_access_token(user),
        expires_in=settings.access_token_minutes * 60,
    )


@app.get("/api/v1/auth/me", response_model=UserResponse)
def me(user: User = Depends(current_user)) -> User:
    return user


def product_response(product: Product) -> ProductResponse:
    return ProductResponse(
        id=product.id,
        sku=product.sku,
        name=product.name,
        description=product.description,
        price=product.price,
        stock_quantity=product.stock_quantity,
        available_quantity=max(0, product.stock_quantity - product.reserved_quantity),
        image_key=product.image_key,
    )


@app.get("/api/v1/products", response_model=list[ProductResponse])
def products(db: Session = Depends(get_db)) -> list[ProductResponse]:
    rows = db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.name).limit(200)).all()
    return [product_response(product) for product in rows]


@app.get("/api/v1/products/{product_id}", response_model=ProductResponse)
def product(product_id: str, db: Session = Depends(get_db)) -> ProductResponse:
    row = db.get(Product, product_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="product not found")
    return product_response(row)


def cart_key(user_id: str) -> str:
    return f"cart:{user_id}"


@app.get("/api/v1/cart", response_model=CartResponse)
def get_cart(user: User = Depends(current_user)) -> CartResponse:
    raw = redis_client.get(cart_key(user.id))
    return CartResponse(items=json.loads(raw) if raw else [])


@app.put("/api/v1/cart", response_model=CartResponse)
def put_cart(item: CartItem, user: User = Depends(current_user), db: Session = Depends(get_db)) -> CartResponse:
    product = db.get(Product, item.product_id)
    if product is None or not product.is_active:
        raise HTTPException(status_code=404, detail="product not found")
    if product.stock_quantity - product.reserved_quantity < item.quantity:
        raise HTTPException(status_code=409, detail="insufficient stock")
    key = cart_key(user.id)
    raw = redis_client.get(key)
    items = {entry["product_id"]: entry for entry in (json.loads(raw) if raw else [])}
    items[item.product_id] = item.model_dump()
    result = list(items.values())
    redis_client.setex(key, settings.cart_ttl_seconds, json.dumps(result))
    return CartResponse(items=result)


@app.delete("/api/v1/cart", status_code=204)
def clear_cart(user: User = Depends(current_user)) -> Response:
    redis_client.delete(cart_key(user.id))
    return Response(status_code=204)


@app.post("/api/v1/storage/{category}", status_code=201)
def upload_object(
    category: str,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
) -> dict[str, str]:
    allowed = {"avatars": {"image/jpeg", "image/png", "image/webp"}, "products": {"image/jpeg", "image/png", "image/webp"}}
    if category not in allowed:
        raise HTTPException(status_code=404, detail="unknown storage category")
    if category == "products" and user.role != "admin":
        raise HTTPException(status_code=403, detail="administrator role required")
    if file.content_type not in allowed[category]:
        raise HTTPException(status_code=415, detail="unsupported media type")
    content = file.file.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file exceeds 5 MiB")
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[file.content_type]
    owner = user.id if category == "avatars" else "catalog"
    key = f"images/{category}/{owner}/{uuid4()}.{extension}"
    try:
        object_storage_client().put_object(
            Bucket=settings.object_storage_bucket,
            Key=key,
            Body=content,
            ContentType=file.content_type,
            ServerSideEncryption="AES256",
        )
    except Exception as exc:
        logger.exception("object upload failed")
        raise HTTPException(status_code=503, detail="object storage unavailable") from exc
    return {"object_key": key}


@app.post(
    "/internal/products/reserve",
    response_model=ReserveResponse,
    dependencies=[Depends(require_internal_token)],
    include_in_schema=False,
)
def reserve_products(payload: ReserveRequest, db: Session = Depends(get_db)) -> ReserveResponse:
    quantities: dict[str, int] = {}
    for item in payload.items:
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity
    products = db.scalars(
        select(Product).where(Product.id.in_(quantities)).with_for_update()
    ).all()
    by_id = {product.id: product for product in products}
    if len(by_id) != len(quantities):
        raise HTTPException(status_code=409, detail="one or more products are unavailable")
    reservation_id = str(uuid4())
    reserved: list[ReservedProduct] = []
    for product_id, quantity in quantities.items():
        product = by_id[product_id]
        if not product.is_active or product.stock_quantity - product.reserved_quantity < quantity:
            db.rollback()
            raise HTTPException(status_code=409, detail=f"insufficient stock for {product.sku}")
        product.reserved_quantity += quantity
        db.add(ProductReservation(reservation_id=reservation_id, product_id=product.id, quantity=quantity))
        reserved.append(ReservedProduct(product_id=product.id, sku=product.sku, name=product.name, quantity=quantity, unit_price=product.price))
    db.commit()
    return ReserveResponse(reservation_id=reservation_id, products=reserved)


@app.post(
    "/internal/products/release",
    status_code=204,
    dependencies=[Depends(require_internal_token)],
    include_in_schema=False,
)
def release_products(payload: ReleaseRequest, db: Session = Depends(get_db)) -> Response:
    reservations = db.scalars(
        select(ProductReservation)
        .where(ProductReservation.reservation_id == payload.reservation_id, ProductReservation.released.is_(False))
        .with_for_update()
    ).all()
    quantities = {reservation.product_id: reservation.quantity for reservation in reservations}
    if not quantities:
        return Response(status_code=204)
    products = db.scalars(select(Product).where(Product.id.in_(quantities)).with_for_update()).all()
    for product in products:
        product.reserved_quantity = max(0, product.reserved_quantity - quantities[product.id])
    for reservation in reservations:
        reservation.released = True
    db.commit()
    return Response(status_code=204)
