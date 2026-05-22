#!/usr/bin/env python3
"""
Bedrock vs Neysa — Inference Cost Comparison
=============================================

Answers: "At what monthly token volume does self-hosting on Neysa beat
paying per-token on Amazon Bedrock?"

Outputs: bedrock_vs_neysa.csv (two sections)

  Section 1 — Model comparison
    For each open-source model available on Bedrock:
    - Bedrock cost ($/1M tokens)
    - Neysa hosting cost ($/1M tokens) based on GPU rental + throughput
    - Break-even: tokens/month at which Neysa becomes cheaper
    - Monthly savings at scale tiers (100M / 1B / 10B tokens)

  Section 2 — Monthly cost at scale
    Side-by-side monthly cost: Bedrock vs Neysa, across volume tiers

NOTES:
  - Proprietary models (Claude, Amazon Nova, Titan) can NOT be self-hosted.
    Their Bedrock prices are shown for awareness only.
  - Throughput estimates are for batch/high-utilization inference.
    Interactive/streaming use cases have ~40-60% lower effective throughput.
  - Bedrock prices shown are on-demand rates. Bedrock also offers
    Provisioned Throughput for committed volume (cheaper at scale).
  - Neysa costs use on-demand rates from the official price list.

Run AFTER fetch_gpu_pricing.py:
    python bedrock_vs_neysa.py
"""

import csv
import os
from datetime import datetime, timezone

# ─── BEDROCK MODEL PRICING (USD per 1M tokens, on-demand) ───────────────────
# Source: AWS Bedrock pricing page (verify at aws.amazon.com/bedrock/pricing)
# Last researched: May 2026
# Format: (display_name, input_$/1M, output_$/1M, can_self_host, notes)

BEDROCK_MODELS = [
    # ── Amazon proprietary (cannot self-host) ────────────────────────────────
    ("Amazon Nova Micro",      0.035,   0.14,  False, "Amazon proprietary"),
    ("Amazon Nova Lite",       0.06,    0.24,  False, "Amazon proprietary"),
    ("Amazon Nova Pro",        0.80,    3.20,  False, "Amazon proprietary"),
    ("Amazon Titan Text Lite", 0.30,    0.40,  False, "Amazon proprietary"),
    ("Amazon Titan Express",   0.80,    1.60,  False, "Amazon proprietary"),

    # ── Anthropic Claude (cannot self-host) ───────────────────────────────────
    ("Claude 3 Haiku",         0.25,    1.25,  False, "Anthropic proprietary"),
    ("Claude 3.5 Haiku",       0.80,    4.00,  False, "Anthropic proprietary"),
    ("Claude 3.5 Sonnet",      3.00,   15.00,  False, "Anthropic proprietary"),
    ("Claude 3 Opus",         15.00,   75.00,  False, "Anthropic proprietary"),
    ("Claude 3.7 Sonnet",      3.00,   15.00,  False, "Anthropic proprietary"),

    # ── Meta Llama (open source — CAN self-host on Neysa) ─────────────────────
    ("Llama 3.1 8B",           0.22,    0.22,  True,  "Open source"),
    ("Llama 3.1 70B",          0.72,    0.72,  True,  "Open source"),
    ("Llama 3.1 405B",         5.32,   16.00,  True,  "Open source"),
    ("Llama 3.2 3B",           0.15,    0.15,  True,  "Open source"),
    ("Llama 3.2 11B",          0.16,    0.16,  True,  "Open source"),
    ("Llama 3.2 90B",          0.72,    0.72,  True,  "Open source"),
    ("Llama 3.3 70B",          0.72,    0.72,  True,  "Open source"),

    # ── Mistral (open source — CAN self-host on Neysa) ────────────────────────
    ("Mistral 7B",             0.15,    0.20,  True,  "Open source"),
    ("Mixtral 8x7B",           0.45,    0.70,  True,  "Open source"),
    ("Mistral Large",          4.00,   12.00,  True,  "Open source"),

    # ── DeepSeek (open source — CAN self-host on Neysa) ──────────────────────
    ("DeepSeek R1",            8.00,   24.00,  True,  "Open source — verify Bedrock availability"),
    ("DeepSeek R1 Distill 70B",0.72,    0.72,  True,  "Open source"),
    ("DeepSeek R1 Distill 8B", 0.22,    0.22,  True,  "Open source"),
]


# ─── NEYSA GPU PRICING (from official price list, May 2026) ──────────────────
# On-demand $/GPU/hr
NEYSA_GPU_HOURLY = {
    "L4":       1.17,
    "L40S":     1.95,
    "H100 SXM": 4.39,
    "H100 NVL": 4.39,
    "H200 SXM": 4.73,
}


# ─── SELF-HOSTING CONFIGURATIONS ─────────────────────────────────────────────
# For each open-source model: recommended Neysa GPU config + throughput estimate
# Throughput = tokens/sec at high batch utilization (~80%)
# Format: model_display_name → (gpu_type, gpu_count, tokens_per_sec)
#
# IMPORTANT: Throughput estimates assume:
#   - fp16 precision (standard production)
#   - High batch utilization (continuous load)
#   - vLLM or TGI inference server
#   - Interactive/streaming will be ~40-60% lower
#   Actual performance varies by batch size, context length, and config.

SELF_HOST_CONFIGS = {
    "Llama 3.1 8B":           ("L40S",     1,   8_000),
    "Llama 3.1 70B":          ("H100 SXM", 2,   2_000),
    "Llama 3.1 405B":         ("H100 SXM", 8,     600),
    "Llama 3.2 3B":           ("L4",       1,  12_000),
    "Llama 3.2 11B":          ("L40S",     1,   6_000),
    "Llama 3.2 90B":          ("H100 SXM", 2,   1_800),
    "Llama 3.3 70B":          ("H100 SXM", 2,   2_000),
    "Mistral 7B":             ("L40S",     1,  10_000),
    "Mixtral 8x7B":           ("H100 SXM", 2,   2_500),
    "Mistral Large":          ("H100 SXM", 4,     800),
    "DeepSeek R1":            ("H200 SXM", 8,     400),
    "DeepSeek R1 Distill 70B":("H100 SXM", 2,   2_000),
    "DeepSeek R1 Distill 8B": ("L40S",     1,   8_000),
}

# Monthly token volume tiers to compare
SCALE_TIERS = [
    ("100M",   100_000_000),
    ("500M",   500_000_000),
    ("1B",   1_000_000_000),
    ("5B",   5_000_000_000),
    ("10B", 10_000_000_000),
    ("50B", 50_000_000_000),
]

# Assume 50/50 input/output split for blended price calculation
INPUT_RATIO  = 0.5
OUTPUT_RATIO = 0.5


# ─── CALCULATIONS ────────────────────────────────────────────────────────────

def blended_bedrock_per_1m(input_price, output_price):
    return round(input_price * INPUT_RATIO + output_price * OUTPUT_RATIO, 4)


def neysa_monthly_cost(gpu_type, gpu_count):
    hourly = NEYSA_GPU_HOURLY.get(gpu_type, 0)
    return round(hourly * gpu_count * 720, 2)  # 720 hours/month


def neysa_monthly_token_capacity(tokens_per_sec):
    """Max tokens per month at given throughput."""
    return int(tokens_per_sec * 3600 * 24 * 30)


def neysa_per_1m_tokens(gpu_type, gpu_count, tokens_per_sec):
    """Effective cost per 1M tokens at max utilization."""
    monthly_cost     = neysa_monthly_cost(gpu_type, gpu_count)
    monthly_capacity = neysa_monthly_token_capacity(tokens_per_sec)
    if monthly_capacity <= 0:
        return None
    return round(monthly_cost / monthly_capacity * 1_000_000, 4)


def break_even_tokens(neysa_monthly, bedrock_per_1m):
    """Monthly tokens at which Neysa total cost = Bedrock total cost."""
    if bedrock_per_1m <= 0:
        return None
    # Neysa is fixed cost; Bedrock scales linearly
    # neysa_monthly = bedrock_per_1m * tokens / 1M
    # tokens = neysa_monthly / bedrock_per_1m * 1M
    tokens = int(neysa_monthly / bedrock_per_1m * 1_000_000)
    return tokens


def format_tokens(n):
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.0f}M"
    return f"{n:,}"


def format_usd(n):
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"${n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"${n:,.0f}"
    return f"${n:.2f}"


# ─── OUTPUT ──────────────────────────────────────────────────────────────────

def write_comparison(path):
    rows = []

    # ── SECTION HEADER ──────────────────────────────────────────────────────
    rows.append({
        "section":                    "MODEL COMPARISON",
        "model":                      "One row per model. Break-even = monthly tokens where Neysa total cost = Bedrock total cost.",
        "can_self_host":              "",
        "bedrock_input_per_1m":       "Bedrock input $/1M tokens",
        "bedrock_output_per_1m":      "Bedrock output $/1M tokens",
        "bedrock_blended_per_1m":     "Bedrock blended $/1M (50/50 in/out)",
        "neysa_gpu_config":           "Recommended Neysa config",
        "neysa_monthly_infra_cost":   "Neysa monthly GPU cost (full month)",
        "neysa_max_tokens_per_month": "Max token capacity on that config",
        "neysa_per_1m_tokens":        "Neysa effective $/1M tokens at capacity",
        "bedrock_vs_neysa_per_1m":    "Bedrock is X% cheaper/more expensive per 1M tokens",
        "break_even_tokens_monthly":  "Monthly tokens where self-hosting starts winning",
        "break_even_monthly_spend":   "Bedrock monthly spend at break-even point",
        "notes":                      "Notes",
    })

    for (model, input_p, output_p, can_host, note) in BEDROCK_MODELS:
        blended   = blended_bedrock_per_1m(input_p, output_p)
        cfg       = SELF_HOST_CONFIGS.get(model)

        if not can_host or cfg is None:
            rows.append({
                "section":                    "model",
                "model":                      model,
                "can_self_host":              "No — proprietary" if not can_host else "Config not defined",
                "bedrock_input_per_1m":       f"${input_p:.4f}",
                "bedrock_output_per_1m":      f"${output_p:.4f}",
                "bedrock_blended_per_1m":     f"${blended:.4f}",
                "neysa_gpu_config":           "—",
                "neysa_monthly_infra_cost":   "—",
                "neysa_max_tokens_per_month": "—",
                "neysa_per_1m_tokens":        "—",
                "bedrock_vs_neysa_per_1m":    "—",
                "break_even_tokens_monthly":  "—",
                "break_even_monthly_spend":   "—",
                "notes":                      note,
            })
            continue

        gpu_type, gpu_count, tps = cfg
        monthly_cost   = neysa_monthly_cost(gpu_type, gpu_count)
        max_capacity   = neysa_monthly_token_capacity(tps)
        neysa_per_1m   = neysa_per_1m_tokens(gpu_type, gpu_count, tps)
        break_even_tok = break_even_tokens(monthly_cost, blended)
        break_even_usd = round(break_even_tok / 1_000_000 * blended, 2) if break_even_tok else None

        if neysa_per_1m and blended > 0:
            diff_pct = round((blended - neysa_per_1m) / blended * 100, 1)
            if diff_pct > 0:
                diff_str = f"Bedrock {diff_pct:.0f}% more expensive at capacity"
            else:
                diff_str = f"Neysa {abs(diff_pct):.0f}% more expensive at capacity"
        else:
            diff_str = "—"

        rows.append({
            "section":                    "model",
            "model":                      model,
            "can_self_host":              "Yes — open source",
            "bedrock_input_per_1m":       f"${input_p:.4f}",
            "bedrock_output_per_1m":      f"${output_p:.4f}",
            "bedrock_blended_per_1m":     f"${blended:.4f}",
            "neysa_gpu_config":           f"{gpu_count}x {gpu_type}",
            "neysa_monthly_infra_cost":   format_usd(monthly_cost),
            "neysa_max_tokens_per_month": format_tokens(max_capacity),
            "neysa_per_1m_tokens":        f"${neysa_per_1m:.4f}" if neysa_per_1m else "—",
            "bedrock_vs_neysa_per_1m":    diff_str,
            "break_even_tokens_monthly":  format_tokens(break_even_tok) if break_even_tok else "—",
            "break_even_monthly_spend":   format_usd(break_even_usd),
            "notes":                      f"{tps:,} tok/s estimated throughput. {note}",
        })

    # ── SECTION 2: MONTHLY COST AT SCALE ─────────────────────────────────────
    rows.append({})  # blank separator
    rows.append({
        "section":                    "MONTHLY COST AT SCALE",
        "model":                      "For open-source models only. Neysa cost = GPU rental (fixed) + assumes sufficient GPU capacity.",
        "can_self_host":              "",
        "bedrock_input_per_1m":       "Monthly tokens",
        "bedrock_output_per_1m":      "Bedrock monthly cost",
        "bedrock_blended_per_1m":     "Neysa monthly GPU cost",
        "neysa_gpu_config":           "Neysa GPU config",
        "neysa_monthly_infra_cost":   "Monthly savings (Bedrock - Neysa)",
        "neysa_max_tokens_per_month": "Savings %",
        "neysa_per_1m_tokens":        "",
        "bedrock_vs_neysa_per_1m":    "",
        "break_even_tokens_monthly":  "",
        "break_even_monthly_spend":   "",
        "notes":                      "Note: Neysa cost scales stepwise (add more GPUs for more capacity). Bedrock scales linearly.",
    })

    # Focus on the most commonly used open-source models for scale analysis
    scale_models = [
        ("Llama 3.1 8B",  "L40S",     1),
        ("Llama 3.1 70B", "H100 SXM", 2),
        ("Llama 3.3 70B", "H100 SXM", 2),
        ("Llama 3.1 405B","H100 SXM", 8),
        ("Mistral 7B",    "L40S",     1),
        ("Mixtral 8x7B",  "H100 SXM", 2),
        ("DeepSeek R1 Distill 70B", "H100 SXM", 2),
    ]

    for model_name, gpu_type, gpu_count in scale_models:
        model_data = next((m for m in BEDROCK_MODELS if m[0] == model_name), None)
        cfg        = SELF_HOST_CONFIGS.get(model_name)
        if not model_data or not cfg:
            continue

        _, input_p, output_p, can_host, _ = model_data
        if not can_host:
            continue

        blended      = blended_bedrock_per_1m(input_p, output_p)
        neysa_cost   = neysa_monthly_cost(gpu_type, gpu_count)
        _, _, tps    = cfg
        max_capacity = neysa_monthly_token_capacity(tps)

        for tier_label, tier_tokens in SCALE_TIERS:
            bedrock_cost = round(tier_tokens / 1_000_000 * blended, 2)

            # Neysa: scale GPUs as needed (round up to next multiple)
            import math
            gpus_needed  = max(gpu_count,
                               gpu_count * math.ceil(tier_tokens / max_capacity))
            neysa_scaled = neysa_monthly_cost(gpu_type, gpus_needed)
            savings      = round(bedrock_cost - neysa_scaled, 2)
            savings_pct  = round(savings / bedrock_cost * 100, 1) if bedrock_cost > 0 else 0

            rows.append({
                "section":                    "scale",
                "model":                      model_name,
                "can_self_host":              "Yes",
                "bedrock_input_per_1m":       tier_label,
                "bedrock_output_per_1m":      format_usd(bedrock_cost),
                "bedrock_blended_per_1m":     format_usd(neysa_scaled),
                "neysa_gpu_config":           f"{gpus_needed}x {gpu_type}",
                "neysa_monthly_infra_cost":   format_usd(savings) if savings > 0 else f"Bedrock cheaper by {format_usd(-savings)}",
                "neysa_max_tokens_per_month": f"{savings_pct:.0f}%" if savings > 0 else "0%",
                "neysa_per_1m_tokens":        "",
                "bedrock_vs_neysa_per_1m":    "",
                "break_even_tokens_monthly":  "",
                "break_even_monthly_spend":   "",
                "notes":                      f"Neysa needs {gpus_needed}x {gpu_type} for {tier_label} tok/mo capacity",
            })

    fieldnames = [
        "section", "model", "can_self_host",
        "bedrock_input_per_1m", "bedrock_output_per_1m", "bedrock_blended_per_1m",
        "neysa_gpu_config", "neysa_monthly_infra_cost", "neysa_max_tokens_per_month",
        "neysa_per_1m_tokens", "bedrock_vs_neysa_per_1m",
        "break_even_tokens_monthly", "break_even_monthly_spend", "notes",
    ]

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    out_path = "bedrock_vs_neysa.csv"
    print("\n" + "=" * 60)
    print("  BEDROCK VS NEYSA — INFERENCE COST COMPARISON")
    print("=" * 60)
    print(f"  Models:       {len(BEDROCK_MODELS)} total "
          f"({sum(1 for m in BEDROCK_MODELS if m[3])} self-hostable)")
    print(f"  Scale tiers:  {', '.join(t for t, _ in SCALE_TIERS)}")
    print(f"  Assumption:   50/50 input/output token split")
    print(f"  Neysa prices: on-demand from official price list")

    write_comparison(out_path)

    size = os.path.getsize(out_path) // 1024 if os.path.exists(out_path) else 0
    print(f"\n  Wrote {out_path} ({size} KB)")
    print(f"\n  TIP: Open in Excel. Filter Section='model' for break-even analysis.")
    print(f"       Filter Section='scale' for monthly cost comparison at volume.")
    print(f"\n  IMPORTANT: Verify Bedrock prices at aws.amazon.com/bedrock/pricing")
    print(f"             Throughput estimates are approximate. Test your actual workload.\n")


if __name__ == "__main__":
    main()
