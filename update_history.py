#!/usr/bin/env python3
"""
GPU Price History Tracker — Neysa Competitive Intelligence

Run this AFTER fetch_gpu_pricing.py. It reads today's gpu_pricing.json
and appends on-demand prices to gpu_pricing_history.csv.

On first run, it seeds the history file with ~3 years of research-based
historical estimates (2022 Q1 → May 2026), so you have a trend to chart
immediately rather than starting from zero.

Usage:
    python update_history.py

Output:
    gpu_pricing_history.csv — one row per provider × GPU type × date

HOW TO CHART IN EXCEL / GOOGLE SHEETS:
    1. Open gpu_pricing_history.csv
    2. Select all → Insert → PivotChart
    3. Rows: date  |  Columns: provider  |  Values: per_gpu_hourly_usd
    4. Filter to one GPU type at a time for clean trend lines

HISTORICAL DATA NOTE:
    Rows with source="historical_estimate" are research-based approximations
    compiled from public pricing pages, press releases, and industry analysis.
    They are directionally correct but not guaranteed to be exact.
    Rows with source="live" come from the daily API fetch and are accurate.

GETTING MORE HISTORICAL DATA:
    - AWS price change history: aws.amazon.com/ec2/pricing/on-demand/
    - GCP pricing history: cloud.google.com/compute/gpus-pricing
    - Wayback Machine snapshots: web.archive.org (search provider pricing pages)
    - computeprices.com — tracks current + recent prices across 25+ providers
    No provider publishes a historical price API, so pre-2026 data requires
    manual research or scraping archived pages.
"""

import csv
import json
import os
from datetime import datetime, timezone, date

HISTORY_FILE  = "gpu_pricing_history.csv"
PRICING_FILE  = "gpu_pricing.json"

FIELDNAMES = [
    "date", "provider", "gpu_type", "price_tier",
    "per_gpu_hourly_usd", "region", "source",
]

# ─── SEED DATA ────────────────────────────────────────────────────────────────
# Research-based historical estimates. Quarterly snapshots where data exists.
# Sources: AWS/GCP/Azure pricing pages, press releases, industry analysis.
# AWS H100 (p5) cut: 44% reduction effective June 1 2025 (confirmed announcement).
# All other changes are approximate based on known pricing evolution.

SEED_DATA = [
    # ── A100 80GB (proxy for "pre-H100 era" GPU pricing) ─────────────────────
    # Available on AWS p4de, GCP A2, Azure ND A100 v4, OCI from ~2021
    {"date": "2022-01-01", "provider": "AWS",   "gpu_type": "A100", "price_tier": "on_demand", "per_gpu_hourly_usd": 4.34,  "region": "us-east-1",   "source": "historical_estimate"},
    {"date": "2022-01-01", "provider": "GCP",   "gpu_type": "A100", "price_tier": "on_demand", "per_gpu_hourly_usd": 3.93,  "region": "us-central1", "source": "historical_estimate"},
    {"date": "2022-01-01", "provider": "Azure", "gpu_type": "A100", "price_tier": "on_demand", "per_gpu_hourly_usd": 4.10,  "region": "eastus",      "source": "historical_estimate"},
    {"date": "2022-01-01", "provider": "OCI",   "gpu_type": "A100", "price_tier": "on_demand", "per_gpu_hourly_usd": 4.00,  "region": "global",      "source": "historical_estimate"},

    {"date": "2022-07-01", "provider": "AWS",   "gpu_type": "A100", "price_tier": "on_demand", "per_gpu_hourly_usd": 4.34,  "region": "us-east-1",   "source": "historical_estimate"},
    {"date": "2022-07-01", "provider": "GCP",   "gpu_type": "A100", "price_tier": "on_demand", "per_gpu_hourly_usd": 3.93,  "region": "us-central1", "source": "historical_estimate"},
    {"date": "2022-07-01", "provider": "Azure", "gpu_type": "A100", "price_tier": "on_demand", "per_gpu_hourly_usd": 4.10,  "region": "eastus",      "source": "historical_estimate"},
    {"date": "2022-07-01", "provider": "OCI",   "gpu_type": "A100", "price_tier": "on_demand", "per_gpu_hourly_usd": 4.00,  "region": "global",      "source": "historical_estimate"},

    {"date": "2023-01-01", "provider": "AWS",   "gpu_type": "A100", "price_tier": "on_demand", "per_gpu_hourly_usd": 4.34,  "region": "us-east-1",   "source": "historical_estimate"},
    {"date": "2023-01-01", "provider": "GCP",   "gpu_type": "A100", "price_tier": "on_demand", "per_gpu_hourly_usd": 3.93,  "region": "us-central1", "source": "historical_estimate"},
    {"date": "2023-01-01", "provider": "Azure", "gpu_type": "A100", "price_tier": "on_demand", "per_gpu_hourly_usd": 3.67,  "region": "eastus",      "source": "historical_estimate"},
    {"date": "2023-01-01", "provider": "OCI",   "gpu_type": "A100", "price_tier": "on_demand", "per_gpu_hourly_usd": 4.00,  "region": "global",      "source": "historical_estimate"},

    {"date": "2023-07-01", "provider": "AWS",   "gpu_type": "A100", "price_tier": "on_demand", "per_gpu_hourly_usd": 4.34,  "region": "us-east-1",   "source": "historical_estimate"},
    {"date": "2023-07-01", "provider": "GCP",   "gpu_type": "A100", "price_tier": "on_demand", "per_gpu_hourly_usd": 3.93,  "region": "us-central1", "source": "historical_estimate"},
    {"date": "2023-07-01", "provider": "Azure", "gpu_type": "A100", "price_tier": "on_demand", "per_gpu_hourly_usd": 3.67,  "region": "eastus",      "source": "historical_estimate"},
    {"date": "2023-07-01", "provider": "OCI",   "gpu_type": "A100", "price_tier": "on_demand", "per_gpu_hourly_usd": 4.00,  "region": "global",      "source": "historical_estimate"},

    # ── H100 SXM (launched AWS/GCP Aug 2023, Azure late 2023, OCI 2024) ─────
    {"date": "2023-10-01", "provider": "AWS",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 12.29, "region": "us-east-1",   "source": "historical_estimate"},
    {"date": "2023-10-01", "provider": "GCP",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 10.80, "region": "us-central1", "source": "historical_estimate"},
    {"date": "2023-10-01", "provider": "RunPod", "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 3.49,  "region": "global",      "source": "historical_estimate"},

    {"date": "2024-01-01", "provider": "AWS",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 12.29, "region": "us-east-1",   "source": "historical_estimate"},
    {"date": "2024-01-01", "provider": "GCP",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 10.40, "region": "us-central1", "source": "historical_estimate"},
    {"date": "2024-01-01", "provider": "Azure",  "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 12.29, "region": "eastus",      "source": "historical_estimate"},
    {"date": "2024-01-01", "provider": "OCI",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 10.00, "region": "global",      "source": "historical_estimate"},
    {"date": "2024-01-01", "provider": "RunPod", "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 3.29,  "region": "global",      "source": "historical_estimate"},

    {"date": "2024-04-01", "provider": "AWS",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 12.29, "region": "us-east-1",   "source": "historical_estimate"},
    {"date": "2024-04-01", "provider": "GCP",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 10.20, "region": "us-central1", "source": "historical_estimate"},
    {"date": "2024-04-01", "provider": "Azure",  "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 12.29, "region": "eastus",      "source": "historical_estimate"},
    {"date": "2024-04-01", "provider": "OCI",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 10.00, "region": "global",      "source": "historical_estimate"},
    {"date": "2024-04-01", "provider": "RunPod", "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 3.09,  "region": "global",      "source": "historical_estimate"},

    {"date": "2024-07-01", "provider": "AWS",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 12.29, "region": "us-east-1",   "source": "historical_estimate"},
    {"date": "2024-07-01", "provider": "GCP",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 9.97,  "region": "us-central1", "source": "historical_estimate"},
    {"date": "2024-07-01", "provider": "Azure",  "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 12.29, "region": "eastus",      "source": "historical_estimate"},
    {"date": "2024-07-01", "provider": "OCI",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 10.00, "region": "global",      "source": "historical_estimate"},
    {"date": "2024-07-01", "provider": "RunPod", "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 2.99,  "region": "global",      "source": "historical_estimate"},

    {"date": "2024-10-01", "provider": "AWS",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 12.29, "region": "us-east-1",   "source": "historical_estimate"},
    {"date": "2024-10-01", "provider": "GCP",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 9.80,  "region": "us-central1", "source": "historical_estimate"},
    {"date": "2024-10-01", "provider": "Azure",  "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 12.29, "region": "eastus",      "source": "historical_estimate"},
    {"date": "2024-10-01", "provider": "OCI",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 10.00, "region": "global",      "source": "historical_estimate"},
    {"date": "2024-10-01", "provider": "RunPod", "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 2.89,  "region": "global",      "source": "historical_estimate"},

    {"date": "2025-01-01", "provider": "AWS",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 12.29, "region": "us-east-1",   "source": "historical_estimate"},
    {"date": "2025-01-01", "provider": "GCP",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 9.80,  "region": "us-central1", "source": "historical_estimate"},
    {"date": "2025-01-01", "provider": "Azure",  "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 12.29, "region": "eastus",      "source": "historical_estimate"},
    {"date": "2025-01-01", "provider": "OCI",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 10.00, "region": "global",      "source": "historical_estimate"},
    {"date": "2025-01-01", "provider": "RunPod", "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 2.89,  "region": "global",      "source": "historical_estimate"},

    # AWS 44% cut effective June 1, 2025 (confirmed press release)
    {"date": "2025-04-01", "provider": "AWS",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 12.29, "region": "us-east-1",   "source": "historical_estimate"},
    {"date": "2025-04-01", "provider": "GCP",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 9.80,  "region": "us-central1", "source": "historical_estimate"},
    {"date": "2025-04-01", "provider": "Azure",  "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 12.29, "region": "eastus",      "source": "historical_estimate"},
    {"date": "2025-04-01", "provider": "OCI",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 10.00, "region": "global",      "source": "historical_estimate"},
    {"date": "2025-04-01", "provider": "RunPod", "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 2.89,  "region": "global",      "source": "historical_estimate"},

    {"date": "2025-07-01", "provider": "AWS",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 6.88,  "region": "us-east-1",   "source": "historical_estimate"},  # post-cut
    {"date": "2025-07-01", "provider": "GCP",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 9.80,  "region": "us-central1", "source": "historical_estimate"},
    {"date": "2025-07-01", "provider": "Azure",  "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 12.29, "region": "eastus",      "source": "historical_estimate"},
    {"date": "2025-07-01", "provider": "OCI",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 10.00, "region": "global",      "source": "historical_estimate"},
    {"date": "2025-07-01", "provider": "RunPod", "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 2.89,  "region": "global",      "source": "historical_estimate"},

    {"date": "2025-10-01", "provider": "AWS",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 6.88,  "region": "us-east-1",   "source": "historical_estimate"},
    {"date": "2025-10-01", "provider": "GCP",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 9.80,  "region": "us-central1", "source": "historical_estimate"},
    {"date": "2025-10-01", "provider": "Azure",  "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 12.29, "region": "eastus",      "source": "historical_estimate"},
    {"date": "2025-10-01", "provider": "OCI",    "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 10.00, "region": "global",      "source": "historical_estimate"},
    {"date": "2025-10-01", "provider": "RunPod", "gpu_type": "H100 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 2.89,  "region": "global",      "source": "historical_estimate"},

    # ── H200 SXM (launched GCP/OCI late 2024, AWS 2025) ──────────────────────
    {"date": "2024-10-01", "provider": "GCP",    "gpu_type": "H200 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 9.31,  "region": "us-central1", "source": "historical_estimate"},
    {"date": "2024-10-01", "provider": "OCI",    "gpu_type": "H200 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 10.00, "region": "global",      "source": "historical_estimate"},
    {"date": "2024-10-01", "provider": "RunPod", "gpu_type": "H200 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 4.39,  "region": "global",      "source": "historical_estimate"},

    {"date": "2025-01-01", "provider": "GCP",    "gpu_type": "H200 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 9.31,  "region": "us-central1", "source": "historical_estimate"},
    {"date": "2025-01-01", "provider": "OCI",    "gpu_type": "H200 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 10.00, "region": "global",      "source": "historical_estimate"},
    {"date": "2025-01-01", "provider": "RunPod", "gpu_type": "H200 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 4.39,  "region": "global",      "source": "historical_estimate"},

    {"date": "2025-07-01", "provider": "AWS",    "gpu_type": "H200 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 7.91,  "region": "us-east-1",   "source": "historical_estimate"},
    {"date": "2025-07-01", "provider": "GCP",    "gpu_type": "H200 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 9.31,  "region": "us-central1", "source": "historical_estimate"},
    {"date": "2025-07-01", "provider": "OCI",    "gpu_type": "H200 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 10.00, "region": "global",      "source": "historical_estimate"},
    {"date": "2025-07-01", "provider": "RunPod", "gpu_type": "H200 SXM", "price_tier": "on_demand", "per_gpu_hourly_usd": 3.99,  "region": "global",      "source": "historical_estimate"},

    # ── L4 (GCP G2 launched late 2023, AWS g6 launched 2024) ─────────────────
    {"date": "2023-10-01", "provider": "GCP",    "gpu_type": "L4", "price_tier": "on_demand", "per_gpu_hourly_usd": 0.70, "region": "us-central1", "source": "historical_estimate"},
    {"date": "2023-10-01", "provider": "RunPod", "gpu_type": "L4", "price_tier": "on_demand", "per_gpu_hourly_usd": 0.50, "region": "global",      "source": "historical_estimate"},

    {"date": "2024-01-01", "provider": "GCP",    "gpu_type": "L4", "price_tier": "on_demand", "per_gpu_hourly_usd": 0.70, "region": "us-central1", "source": "historical_estimate"},
    {"date": "2024-01-01", "provider": "RunPod", "gpu_type": "L4", "price_tier": "on_demand", "per_gpu_hourly_usd": 0.48, "region": "global",      "source": "historical_estimate"},

    {"date": "2024-07-01", "provider": "AWS",    "gpu_type": "L4", "price_tier": "on_demand", "per_gpu_hourly_usd": 0.80, "region": "us-east-1",   "source": "historical_estimate"},
    {"date": "2024-07-01", "provider": "GCP",    "gpu_type": "L4", "price_tier": "on_demand", "per_gpu_hourly_usd": 0.70, "region": "us-central1", "source": "historical_estimate"},
    {"date": "2024-07-01", "provider": "RunPod", "gpu_type": "L4", "price_tier": "on_demand", "per_gpu_hourly_usd": 0.44, "region": "global",      "source": "historical_estimate"},

    {"date": "2025-01-01", "provider": "AWS",    "gpu_type": "L4", "price_tier": "on_demand", "per_gpu_hourly_usd": 0.80, "region": "us-east-1",   "source": "historical_estimate"},
    {"date": "2025-01-01", "provider": "GCP",    "gpu_type": "L4", "price_tier": "on_demand", "per_gpu_hourly_usd": 0.56, "region": "us-central1", "source": "historical_estimate"},
    {"date": "2025-01-01", "provider": "RunPod", "gpu_type": "L4", "price_tier": "on_demand", "per_gpu_hourly_usd": 0.44, "region": "global",      "source": "historical_estimate"},

    # ── L40S (launched ~mid 2024 across providers) ────────────────────────────
    {"date": "2024-07-01", "provider": "AWS",    "gpu_type": "L40S", "price_tier": "on_demand", "per_gpu_hourly_usd": 1.86, "region": "us-east-1", "source": "historical_estimate"},
    {"date": "2024-07-01", "provider": "OCI",    "gpu_type": "L40S", "price_tier": "on_demand", "per_gpu_hourly_usd": 3.50, "region": "global",    "source": "historical_estimate"},
    {"date": "2024-07-01", "provider": "RunPod", "gpu_type": "L40S", "price_tier": "on_demand", "per_gpu_hourly_usd": 0.86, "region": "global",    "source": "historical_estimate"},

    {"date": "2025-01-01", "provider": "AWS",    "gpu_type": "L40S", "price_tier": "on_demand", "per_gpu_hourly_usd": 1.86, "region": "us-east-1", "source": "historical_estimate"},
    {"date": "2025-01-01", "provider": "OCI",    "gpu_type": "L40S", "price_tier": "on_demand", "per_gpu_hourly_usd": 3.50, "region": "global",    "source": "historical_estimate"},
    {"date": "2025-01-01", "provider": "RunPod", "gpu_type": "L40S", "price_tier": "on_demand", "per_gpu_hourly_usd": 0.86, "region": "global",    "source": "historical_estimate"},

    {"date": "2025-07-01", "provider": "AWS",    "gpu_type": "L40S", "price_tier": "on_demand", "per_gpu_hourly_usd": 1.86, "region": "us-east-1", "source": "historical_estimate"},
    {"date": "2025-07-01", "provider": "OCI",    "gpu_type": "L40S", "price_tier": "on_demand", "per_gpu_hourly_usd": 3.50, "region": "global",    "source": "historical_estimate"},
    {"date": "2025-07-01", "provider": "RunPod", "gpu_type": "L40S", "price_tier": "on_demand", "per_gpu_hourly_usd": 0.86, "region": "global",    "source": "historical_estimate"},
]


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def load_existing_history():
    """Return set of (date, provider, gpu_type, price_tier) already in file."""
    if not os.path.exists(HISTORY_FILE):
        return set()
    existing = set()
    with open(HISTORY_FILE, newline="") as f:
        for row in csv.DictReader(f):
            existing.add((
                row.get("date", ""),
                row.get("provider", ""),
                row.get("gpu_type", ""),
                row.get("price_tier", ""),
            ))
    return existing


def write_rows(rows, append=True):
    mode     = "a" if append else "w"
    is_new   = not os.path.exists(HISTORY_FILE) or not append
    with open(HISTORY_FILE, mode, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


# ─── SEED ────────────────────────────────────────────────────────────────────

def seed_history(existing):
    """Write seed data rows that aren't already in the file."""
    new_rows = []
    for row in SEED_DATA:
        key = (row["date"], row["provider"], row["gpu_type"], row["price_tier"])
        if key not in existing:
            new_rows.append(row)

    if new_rows:
        print(f"  Seeding {len(new_rows)} historical data points...")
        write_rows(new_rows, append=bool(existing))
    else:
        print("  Seed data already present, skipping.")
    return len(new_rows)


# ─── DAILY APPEND ────────────────────────────────────────────────────────────

def append_today(existing):
    """Read gpu_pricing.json and append today's on-demand prices to history."""
    if not os.path.exists(PRICING_FILE):
        print(f"  ✗ {PRICING_FILE} not found — run fetch_gpu_pricing.py first")
        return 0

    with open(PRICING_FILE) as f:
        pricing = json.load(f)

    today     = date.today().isoformat()
    new_rows  = []

    for prov_key, prov_data in pricing.get("providers", {}).items():
        if prov_data.get("error"):
            continue
        provider = prov_data.get("provider", prov_key)

        # Track on_demand, 12mo, 36mo, spot — one row per GPU type (best price per tier)
        # Group by (gpu_type, price_tier) and keep lowest per_gpu price
        buckets = {}
        for sku in prov_data.get("skus", []):
            gpu_type  = sku.get("gpu_type")
            tier      = sku.get("price_tier", "on_demand")
            price     = sku.get("per_gpu_hourly_usd")
            region    = sku.get("region", "")

            if not gpu_type or not price:
                continue
            if tier not in ("on_demand", "12mo", "36mo", "spot"):
                continue

            key = (gpu_type, tier)
            existing_entry = buckets.get(key)
            if existing_entry is None or float(price) < float(existing_entry["per_gpu_hourly_usd"]):
                buckets[key] = {
                    "date":               today,
                    "provider":           provider,
                    "gpu_type":           gpu_type,
                    "price_tier":         tier,
                    "per_gpu_hourly_usd": round(float(price), 4),
                    "region":             region,
                    "source":             "live",
                }

        for (gpu_type, tier), row in buckets.items():
            hist_key = (today, provider, gpu_type, tier)
            if hist_key not in existing:
                new_rows.append(row)

    if new_rows:
        write_rows(new_rows, append=True)
        print(f"  Appended {len(new_rows)} live price rows for {today}")
    else:
        print(f"  No new rows for {today} (already up to date)")

    return len(new_rows)


# ─── SUMMARY ─────────────────────────────────────────────────────────────────

def print_summary():
    """Print a quick preview of what's in the history file."""
    if not os.path.exists(HISTORY_FILE):
        return
    rows = []
    with open(HISTORY_FILE, newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        return

    dates     = sorted(set(r["date"] for r in rows))
    providers = sorted(set(r["provider"] for r in rows))
    gpu_types = sorted(set(r["gpu_type"] for r in rows))

    print(f"\n  History file: {HISTORY_FILE}")
    print(f"  Total rows  : {len(rows)}")
    print(f"  Date range  : {dates[0]} → {dates[-1]}  ({len(dates)} distinct dates)")
    print(f"  Providers   : {', '.join(providers)}")
    print(f"  GPU types   : {', '.join(gpu_types)}")
    print(f"\n  TIP: Open in Excel → Insert → PivotChart")
    print(f"       Axis: date  |  Series: provider  |  Values: per_gpu_hourly_usd")
    print(f"       Filter by gpu_type to see individual GPU trends")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("  GPU PRICE HISTORY UPDATE")
    print("=" * 60)

    existing = load_existing_history()
    is_new   = len(existing) == 0
    print(f"  Existing rows: {len(existing)}")

    # 1. Seed historical data (runs once, skipped if data already present)
    seeded = seed_history(existing)

    # Reload existing after seeding
    if seeded > 0:
        existing = load_existing_history()

    # 2. Append today's live data
    appended = append_today(existing)

    # 3. Summary
    print_summary()

    size_kb = os.path.getsize(HISTORY_FILE) // 1024 if os.path.exists(HISTORY_FILE) else 0
    print(f"\n  Wrote {HISTORY_FILE} ({size_kb} KB)\n")


if __name__ == "__main__":
    main()
