#!/usr/bin/env python3
"""
Neysa Competitor SKU Matcher
============================
Given Neysa's own GPU SKUs and a competitor GPU-pricing JSON snapshot
(produced by fetch_gpu_pricing.py), find for each Neysa SKU the closest
competitor SKU per provider.

Matching logic
--------------
1. GPU TYPE  : exact match first. If a competitor has no SKU of that GPU
               type, fall back to the TWO nearest GPU types on a capability
               ladder (L4 < L40S < H100 NVL ~ H100 SXM < H200 SXM < B200 < B300).
               (A100 is intentionally excluded — Neysa does not sell it.)
2. GPU COUNT : prefer the same node size; otherwise compare on a normalised
               per-GPU basis (all competitor data is already per-GPU/hr).
3. REGION    : India regions preferred (ap-south-1, asia-south1, southindia),
               then US, then global.
4. TIER      : map Neysa tiers -> competitor tiers. When a competitor lacks a
               tier, SYNTHESISE it two ways and flag it `*est`:
                 (a) hourly rate  = nearest-available competitor rate scaled
                                    by Neysa's own tier-to-tier ratio
                 (b) contract TCV = synthesised hourly * hours-in-term
               The note records which competitor tier was used as the base.

All money is normalised to USD/GPU/hour as the primary comparison basis.
Neysa INR is converted with USD_INR (edit below). Neysa committed tiers are
stored as MONTHLY INR totals in the sheet; we convert them to per-GPU/hr.

Usage
-----
    python match_competitors.py \
        --neysa neysa_skus.json \
        --competitors gpu_pricing.json \
        --out comparison.xlsx
"""

import argparse, json, math
from collections import defaultdict

# ─── CONFIG ──────────────────────────────────────────────────────────────────
USD_INR = 94.61         # <-- edit to current FX. Used to convert Neysa INR -> USD.
HOURS_PER_MONTH = 730   # standard cloud billing month (365*24/12)

# Operating system. Neysa VMs are Ubuntu only, so we compare against the
# competitor's Linux/Ubuntu pricing. Other-OS SKUs are still parsed and kept
# (visible in the Detail tab) but excluded from the headline comparison unless
# OS_FILTER is set to None.
OS_FILTER = "Linux"     # set to None to compare across all operating systems

# Capability ladder (low -> high). A100 deliberately omitted.
# H100 NVL and H100 SXM treated as adjacent rungs.
GPU_LADDER = ["L4", "L40S", "H100 NVL", "H100 SXM", "H200 SXM", "B200", "B300"]

# Neysa tiers in months. on_demand handled separately (term = None).
NEYSA_TERM_MONTHS = {"1mo": 1, "6mo": 6, "12mo": 12, "24mo": 24, "36mo": 36}

# Competitor tiers we may see in the snapshot.
COMPETITOR_TIERS = ["on_demand", "1mo", "6mo", "12mo", "24mo", "36mo", "spot"]

# Region priority buckets.
INDIA_REGIONS = {"ap-south-1", "asia-south1", "southindia"}
US_REGIONS = {"us-east-1", "us-east-2", "us-central1", "eastus", "eastus2", "westus2"}


def region_rank(region):
    if region in INDIA_REGIONS:
        return 0
    if region in US_REGIONS:
        return 1
    if region == "global" or region == "" or region is None:
        return 2
    return 3


def ladder_distance(a, b):
    """Distance between two GPU types on the capability ladder. inf if unknown."""
    try:
        return abs(GPU_LADDER.index(a) - GPU_LADDER.index(b))
    except ValueError:
        return math.inf


# ─── LOAD NEYSA SKUS ─────────────────────────────────────────────────────────
def load_neysa(path):
    """
    Expected JSON: list of objects like
      {"gpu_type": "H100 SXM", "gpu_count": 1,
       "on_demand_inr_hr": 395,
       "monthly_inr": {"1mo":205000,"6mo":192700,"12mo":181138,
                       "24mo":170270,"36mo":160054}}
    Produces per-GPU/hr USD for every tier.
    """
    with open(path) as f:
        raw = json.load(f)

    skus = []
    for s in raw:
        gpu = s["gpu_type"]
        count = s["gpu_count"]
        tiers = {}  # tier -> usd per-gpu per-hr

        od = s.get("on_demand_inr_hr")
        if od:
            # on-demand is hourly INR for the whole node
            tiers["on_demand"] = (od / USD_INR) / count

        for tier, monthly_inr in (s.get("monthly_inr") or {}).items():
            if not monthly_inr:
                continue
            node_hr_usd = (monthly_inr / HOURS_PER_MONTH) / USD_INR
            tiers[tier] = node_hr_usd / count

        skus.append({
            "gpu_type": gpu,
            "gpu_count": count,
            "label": s.get("label", f"{count} x {gpu}"),
            "os": s.get("os", "Ubuntu"),
            "usd_per_gpu_hr": tiers,   # dict by tier
        })
    return skus


def neysa_tier_ratios(neysa_sku):
    """Ratio of each committed tier to the 12mo anchor (for synthesising)."""
    t = neysa_sku["usd_per_gpu_hr"]
    anchor = t.get("12mo")
    ratios = {}
    if anchor:
        for tier, val in t.items():
            ratios[tier] = val / anchor
    return ratios   # e.g. {"24mo": 0.94, "36mo": 0.88, ...}


# ─── LOAD COMPETITORS ────────────────────────────────────────────────────────
def load_competitors(path):
    """Flatten the fetch_gpu_pricing.py JSON into per-provider SKU lists.

    OS handling: each SKU may carry an `os` field. If absent, the provider's
    compute is Linux by default (RunPod, Vultr, OCI bare GPU, GCP/AWS GPU
    instances are all Linux unless a Windows licence is added). When OS_FILTER
    is set, non-matching-OS SKUs are tagged but excluded from headline matching.
    """
    with open(path) as f:
        data = json.load(f)
    providers = {}
    for key, pdata in data.get("providers", {}).items():
        name = pdata.get("provider", key)
        rows = []
        for sku in pdata.get("skus", []):
            pg = sku.get("per_gpu_hourly_usd")
            if not pg:
                continue
            os_name = sku.get("os") or "Linux"   # default: Linux/Ubuntu
            rows.append({
                "gpu_type": sku.get("gpu_type"),
                "gpu_count": sku.get("gpu_count", 1),
                "region": sku.get("region", ""),
                "tier": sku.get("price_tier", "on_demand"),
                "per_gpu_hr": float(pg),
                "os": os_name,
                "name": (sku.get("sku") or sku.get("instance_type")
                         or sku.get("display_name") or sku.get("part_number")
                         or sku.get("id") or sku.get("description") or ""),
            })
        providers[name] = rows
    return providers


def _os_ok(r):
    return OS_FILTER is None or r.get("os", "Linux") == OS_FILTER


def best_sku_for(provider_rows, gpu_type, target_count, tier):
    """
    Among provider rows of a given gpu_type & tier, pick the best match:
    region priority, then closest GPU count, then cheapest per-GPU.
    Honours OS_FILTER. Returns the row dict or None.
    """
    cands = [r for r in provider_rows
             if r["gpu_type"] == gpu_type and r["tier"] == tier and _os_ok(r)]
    if not cands:
        return None
    cands.sort(key=lambda r: (
        region_rank(r["region"]),
        abs((r["gpu_count"] or 1) - target_count),
        r["per_gpu_hr"],
    ))
    return cands[0]


def available_tiers_for(provider_rows, gpu_type):
    return {r["tier"] for r in provider_rows
            if r["gpu_type"] == gpu_type and _os_ok(r)}


# Preferred base tier to synthesise FROM, per missing tier.
SYNTH_BASE_PREFERENCE = {
    "on_demand": ["on_demand", "1mo", "12mo", "6mo", "36mo"],
    "1mo":       ["1mo", "6mo", "12mo", "on_demand", "36mo"],
    "6mo":       ["6mo", "12mo", "1mo", "36mo", "on_demand"],
    "12mo":      ["12mo", "6mo", "36mo", "1mo", "on_demand"],
    "24mo":      ["12mo", "36mo", "6mo", "1mo", "on_demand"],
    "36mo":      ["36mo", "12mo", "24mo", "6mo", "1mo", "on_demand"],
}


def resolve_tier(provider_rows, gpu_type, target_count, tier, neysa_ratios):
    """
    Return (per_gpu_hr, status, note).
      status: "exact" | "synth"
    For synth, scale a base competitor tier by Neysa's own ratio between
    the base tier and the requested tier.
    """
    direct = best_sku_for(provider_rows, gpu_type, target_count, tier)
    if direct:
        return direct["per_gpu_hr"], "exact", direct["name"], direct["region"], direct["gpu_count"], direct.get("os", "Linux")

    # need to synthesise
    avail = available_tiers_for(provider_rows, gpu_type)
    for base_tier in SYNTH_BASE_PREFERENCE.get(tier, ["on_demand"]):
        if base_tier in avail:
            base = best_sku_for(provider_rows, gpu_type, target_count, base_tier)
            if not base:
                continue
            # scale by neysa ratio (requested / base) if we have both
            r_req = neysa_ratios.get(tier)
            r_base = neysa_ratios.get(base_tier)
            if r_req and r_base:
                scale = r_req / r_base
            else:
                scale = 1.0  # no ratio info -> assume flat
            synth = base["per_gpu_hr"] * scale
            note = f"est from {base['name'] or base_tier} ({base_tier}, x{scale:.3f})"
            return synth, "synth", note, base["region"], base["gpu_count"], base.get("os", "Linux")
    return None, "none", "", "", None, None


# ─── MATCH ───────────────────────────────────────────────────────────────────
def match_all(neysa_skus, competitors):
    """
    For each Neysa SKU, for each provider, produce comparison rows for every
    Neysa tier. Includes GPU-type fallback (2 nearest) when provider lacks the
    exact GPU type.
    """
    results = []
    all_tiers = ["on_demand"] + list(NEYSA_TERM_MONTHS.keys())

    for nsku in neysa_skus:
        gpu = nsku["gpu_type"]
        count = nsku["gpu_count"]
        ratios = neysa_tier_ratios(nsku)

        for prov_name, rows in competitors.items():
            prov_gpu_types = {r["gpu_type"] for r in rows}

            # decide which GPU type(s) to compare against
            if gpu in prov_gpu_types:
                match_types = [(gpu, "exact-gpu")]
            else:
                # two nearest on the ladder that the provider actually has
                ranked = sorted(
                    [g for g in prov_gpu_types if ladder_distance(gpu, g) != math.inf],
                    key=lambda g: ladder_distance(gpu, g)
                )
                match_types = [(g, f"nearest-gpu(\u0394{ladder_distance(gpu,g)})")
                               for g in ranked[:2]]
                if not match_types:
                    continue

            for cmp_gpu, gpu_match_kind in match_types:
                for tier in all_tiers:
                    neysa_price = nsku["usd_per_gpu_hr"].get(tier)
                    per_gpu, status, note, region, ccount, cos = resolve_tier(
                        rows, cmp_gpu, count, tier, ratios)
                    if per_gpu is None:
                        continue

                    term_months = NEYSA_TERM_MONTHS.get(tier)
                    tcv = (per_gpu * HOURS_PER_MONTH * term_months
                           if term_months else None)
                    neysa_tcv = (neysa_price * HOURS_PER_MONTH * term_months
                                 if (term_months and neysa_price) else None)

                    delta_pct = None
                    if neysa_price and per_gpu:
                        delta_pct = (neysa_price - per_gpu) / per_gpu * 100

                    results.append({
                        "neysa_sku": nsku["label"],
                        "neysa_gpu": gpu,
                        "neysa_count": count,
                        "neysa_os": nsku.get("os", "Ubuntu"),
                        "tier": tier,
                        "neysa_usd_gpu_hr": round(neysa_price, 4) if neysa_price else None,
                        "neysa_tcv_usd": round(neysa_tcv, 0) if neysa_tcv else None,
                        "provider": prov_name,
                        "gpu_match": gpu_match_kind,
                        "cmp_gpu": cmp_gpu,
                        "cmp_count": ccount,
                        "cmp_os": cos,
                        "cmp_region": region,
                        "cmp_usd_gpu_hr": round(per_gpu, 4),
                        "cmp_tcv_usd": round(tcv, 0) if tcv else None,
                        "tier_status": status,            # exact | synth
                        "neysa_vs_cmp_pct": round(delta_pct, 1) if delta_pct is not None else None,
                        "note": note if status == "synth" else "",
                    })
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--neysa", required=True)
    ap.add_argument("--competitors", required=True)
    ap.add_argument("--out", default="comparison.xlsx")
    ap.add_argument("--csv", default=None)
    args = ap.parse_args()

    neysa = load_neysa(args.neysa)
    comps = load_competitors(args.competitors)
    rows = match_all(neysa, comps)

    if not rows:
        print("WARNING: no comparison rows produced. Check inputs.")
        return

    # Always write CSV (default name if not given)
    csv_path = args.csv or args.out.rsplit(".", 1)[0] + ".csv"
    import csv
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"Wrote {csv_path} ({len(rows)} rows)")

    try:
        from build_xlsx import build
        build(rows, neysa, args.out)
        print(f"Wrote {args.out}")
    except Exception as e:
        print(f"xlsx build skipped/failed ({e}); CSV is still available.")


if __name__ == "__main__":
    main()
