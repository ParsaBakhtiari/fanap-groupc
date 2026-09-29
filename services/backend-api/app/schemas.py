from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)

    @field_validator("password")
    @classmethod
    def require_password_variety(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("password must not exceed 72 UTF-8 bytes")
        if not (any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdigit() for c in value)):
            raise ValueError("password must contain upper-case, lower-case, and numeric characters")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: EmailStr
    role: str


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    sku: str
    name: str
    description: str
    price: Decimal
    stock_quantity: int
    available_quantity: int
    image_key: str | None


class CartItem(BaseModel):
    product_id: str = Field(min_length=36, max_length=36)
    quantity: int = Field(ge=1, le=100)


class CartResponse(BaseModel):
    items: list[CartItem]


class ReservationItem(BaseModel):
    product_id: str = Field(min_length=36, max_length=36)
    quantity: int = Field(ge=1, le=100)


class ReserveRequest(BaseModel):
    items: list[ReservationItem] = Field(min_length=1, max_length=50)


class ReservedProduct(BaseModel):
    product_id: str
    sku: str
    name: str
    quantity: int
    unit_price: Decimal


class ReserveResponse(BaseModel):
    reservation_id: str
    products: list[ReservedProduct]


class ReleaseRequest(BaseModel):
    reservation_id: str
    items: list[ReservationItem] = Field(min_length=1, max_length=50)
