from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

Money = Numeric(10, 2, asdecimal=True)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (CheckConstraint("retail_price > 0", name="ck_product_retail_positive"),)

    sku: Mapped[str] = mapped_column(String(32), primary_key=True)
    brand: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(160))
    retail_price: Mapped[Decimal] = mapped_column(Money)
    image_url: Mapped[str] = mapped_column(String(512))
    # Segmento de la app: "men" | "women" | "unisex".
    gender: Mapped[str] = mapped_column(String(8), default="unisex", server_default="unisex")
    colorway: Mapped[str | None] = mapped_column(String(120), default=None)

    # lazy="raise": en async toda carga debe ser explícita (selectinload).
    offers: Mapped[list[StoreOffer]] = relationship(
        back_populates="product", cascade="all, delete-orphan", lazy="raise",
    )
    history: Mapped[list[PriceHistory]] = relationship(
        back_populates="product", cascade="all, delete-orphan", lazy="raise",
    )


class StoreOffer(Base):
    __tablename__ = "store_offers"
    __table_args__ = (
        UniqueConstraint("product_sku", "store_name", "size", name="uq_offer_product_store_size"),
        CheckConstraint("price > 0", name="ck_offer_price_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_sku: Mapped[str] = mapped_column(ForeignKey("products.sku", ondelete="CASCADE"), index=True)
    store_name: Mapped[str] = mapped_column(String(64))
    size: Mapped[str] = mapped_column(String(8))
    price: Mapped[Decimal] = mapped_column(Money)
    original_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True, default=None)
    in_stock: Mapped[bool] = mapped_column(Boolean)
    affiliate_url: Mapped[str] = mapped_column(String(1024))
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    product: Mapped[Product] = relationship(back_populates="offers", lazy="raise")


class PriceHistory(Base):
    __tablename__ = "price_history"
    __table_args__ = (
        UniqueConstraint("product_sku", "date", name="uq_history_product_date"),
        Index("ix_history_product_date", "product_sku", "date"),
        CheckConstraint("price > 0", name="ck_history_price_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_sku: Mapped[str] = mapped_column(ForeignKey("products.sku", ondelete="CASCADE"))
    date: Mapped[date] = mapped_column(Date)
    price: Mapped[Decimal] = mapped_column(Money)

    product: Mapped[Product] = relationship(back_populates="history", lazy="raise")
