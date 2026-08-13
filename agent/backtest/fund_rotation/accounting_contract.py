"""Shared daily accounting contract constants for fund-rotation backtests."""

from __future__ import annotations


ACCOUNTING_CONTRACT_VERSION = "daily_accounting_v1"
DAILY_ACCOUNTING_EVENT_ORDER: tuple[str, ...] = (
    "load_beginning_account_state",
    "apply_corporate_actions",
    "normalize_comparable_prior_close",
    "create_or_replace_parent_orders",
    "execute_sell_attempts",
    "execute_buy_attempts",
    "closing_valuation",
    "persist_ending_account_state",
    "compute_pnl_and_reconciliation",
)
