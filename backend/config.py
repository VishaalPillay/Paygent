"""Shared configuration. Environment-driven, no secrets in code."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Razorpay test mode. Never put a live key here.
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

# Demo controls.
#   DEMO_DROP_WEBHOOK=1  accept the webhook and deliberately discard it, so a real
#                        on-stage payment produces a real broken ledger state.
#   DEMO_MODE=replay     serve recorded fixtures if the LLM rate-limits.
DEMO_DROP_WEBHOOK = os.getenv("DEMO_DROP_WEBHOOK", "0") == "1"
DEMO_MODE = os.getenv("DEMO_MODE", "live")

# Signature verification is skipped only when no secret is configured, so local
# curl testing works without one. Any deployment with a secret set enforces it.
VERIFY_WEBHOOK_SIGNATURE = bool(RAZORPAY_WEBHOOK_SECRET)
