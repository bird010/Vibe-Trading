"""Shared daily accounting contract constants for fund-rotation backtests."""

from __future__ import annotations


ACCOUNTING_CONTRACT_VERSION = "daily_accounting_v1"
DAILY_ACCOUNTING_EVENT_ORDER: tuple[str, ...] = (
    "corporate_actions",
    "fills",
    "valuation",
    "residual_order_carry",
)
