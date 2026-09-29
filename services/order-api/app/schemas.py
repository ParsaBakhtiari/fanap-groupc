from typing import Literal

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from .models import OrderStatus


class CheckoutItem(BaseModel):
    product_id: str = Field(min_length=36, max_length=36)
    quantity: int = Field(ge=1, le=100)


class CheckoutRequest(BaseModel):
    items: list[CheckoutItem] = Field(min_length=1, max_length=50)
    shipping_address: str = Field(min_length=10, max_length=500)
    currency: str = Field(default="EUR", pattern="^[A-Z]{3}$")


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    product_id: str
    sku: str
    product_name: str
    quantity: int
    unit_price: Decimal


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: OrderStatus
    total_amount: Decimal
    currency: str
    shipping_address: str
    invoice_key: str | None
    created_at: datetime
    items: list[OrderItemResponse]
