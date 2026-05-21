#!/usr/bin/env python3
"""
GPU Pricing Fetcher v5 — Neysa Competitive Intelligence

Fixes vs v4:
- Azure: uses preview API (returns more SKUs including L4)
- Azure: reads Savings Plans inline instead of separate reservation query
- Azure: added eastus2 + westus2 regions (needed for H200)
- Azure: L4 SKUs corrected to full-GPU only (NV36/NV72, not fractional NV6/12/18)
- All other providers unchanged

Outputs:
  gpu_pricing.json              — full raw data
  gpu_pricing_raw.csv           — every SKU, every provider, all rows
  gpu_pricing_comparison.csv    — clean pivot table, Neysa vs competitors
"""

import os, sys, csv, json, re
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    sys.exit("Run: pip install requests boto3")


# ─── NEYSA PRICING (from official price list, May 2026) ─────────────────────
NEYSA = {
    "L4":      {"on_demand": 1.17, "1mo": 0.78, "6mo": 0.73, "12mo": 0.68, "24mo": 0.64, "36mo": 0.59},
    "L40S":    {"on_demand": 1.95, "1mo": 1.30, "6mo": 1.21, "12mo": 1.13, "24mo": 1.06, "36mo": 0.99},
    "H100 SXM":{"on_demand": 4.39, "1mo": 3.17, "6mo": 2.98, "12mo": 2.80, "24mo": 2.63, "36mo": 2.47},
    "H100 NVL":{"on_demand": 4.39, "1mo": 3.17, "6mo": 2.98, "12mo": 2.80, "24mo": 2.63, "36mo": 2.47},
    "H200 SXM":{"on_demand": 4.73, "1mo": 3.32, "6mo": 3.12, "12mo": 2.93, "24mo": 2.76, "36mo": 2.59},
    "B200":    {"on_demand": None, "1mo": None, "6mo": None, "12mo": None, "24mo": None, "36mo": None},
    "B300":    {"on_demand": None, "1mo": None, "6mo": None, "12mo": None, "24mo": None, "36mo": None},
}

GPU_TYPES = ["L4", "L40S", "H100 SXM", "H100 NVL", "H200 SXM", "B200", "B300"]

INDIA_REGIONS = {"azure": "southindia", "aws": "ap-south-1",  "gcp": "asia-south1"}
US_REGIONS    = {"azure": "eastus",     "aws": "us-east-1",   "gcp": "us-central1"}

REGIONS = {
    # FIX: added eastus2 + westus2 — needed for H200 and newer SKUs
    "azure": ["eastus", "eastus2", "westus2", "southindia"],
    "aws":   ["us-east-1", "ap-south-1"],
    "gcp":   ["us-central1", "asia-south1"],
}


# ─── PROVIDER 1: AZURE ───────────────────────────────────────────────────────
AZURE_SKUS = {
    # H100 SXM
    "Standard_ND96isr_H100_v5":   ("H100 SXM", 8),
    # H100 NVL
    "Standard_NC40ads_H100_v5":   ("H100 NVL", 1),
    "Standard_NC80adis_H100_v5":  ("H100 NVL", 2),
    # H200 SXM — lives in eastus2/westus2, not eastus
    "Standard_ND96isr_H200_v5":   ("H200 SXM", 8),
    # FIX: L4 — full GPU SKUs only. NV36=1 full L4, NV72=2 full L4s.
    # NV6/NV12/NV18 are fractional vGPU slices — excluded, not comparable to Neysa.
    "Standard_NV36ads_L4_v5":     ("L4", 1),
    "Standard_NV36adms_L4_v5":    ("L4", 1),
    "Standard_NV72ads_L4_v5":     ("L4", 2),
    # A100
    "Standard_NC24ads_A100_v4":   ("A100", 1),
    "Standard_NC48ads_A100_v4":   ("A100", 2),
    "Standard_NC96ads_A100_v4":   ("A100", 4),
    "Standard_ND96amsr_A100_v4":  ("A100", 8),
    # B200 — speculative, skips silently if not yet in pricing API
    "Standard_ND_GB200_v6":       ("B200", 4),
}


def fetch_azure():
    # FIX: preview API version returns more complete SKU coverage
    base = "https://prices.azure.com/api/retail/prices"
    api_ver = {"api-version": "2023-01-01-preview"}
    skus = []
    seen = set()  # deduplicate across regions

    for region in REGIONS["azure"]:
        for arm_sku, (gpu, count) in AZURE_SKUS.items():
            flt = (f"serviceName eq 'Virtual Machines' and armSkuName eq '{arm_sku}' "
                   f"and armRegionName eq '{region}' and priceType eq 'Consumption'")
            try:
                items = requests.get(base, params={**api_ver, "$filter": flt},
                                     timeout=30).json().get("Items", [])
                items = [i for i in items
                         if "Windows" not in i.get("productName", "")
                         and "Spot" not in i.get("meterName", "")
                         and "Low Priority" not in i.get("meterName", "")]
                if not items:
                    continue
                items.sort(key=lambda x: x.get("retailPrice", 999))
                item = items[0]
                total = item["retailPrice"]

                # On-demand
                key = (arm_sku, region, "on_demand")
                if key not in seen:
                    seen.add(key)
                    skus.append({
                        "sku": arm_sku, "gpu_type": gpu, "gpu_count": count,
                        "region": region, "price_tier": "on_demand",
                        "total_hourly_usd": round(total, 4),
                        "per_gpu_hourly_usd": round(total / count, 4),
                    })

                # FIX: Savings Plans returned inline — much more reliable than
                # separate reservation queries
                for sp in item.get("savingsPlan", []):
                    term = sp.get("term", "")
                    sp_price = sp.get("retailPrice") or sp.get("unitPrice")
                    if not sp_price:
                        continue
                    tier = ("12mo" if "1 Year" in term else
                            "36mo" if "3 Year" in term else None)
                    if not tier:
                        continue
                    key2 = (arm_sku, region, tier)
                    if key2 not in seen:
                        seen.add(key2)
                        skus.append({
                            "sku": arm_sku, "gpu_type": gpu, "gpu_count": count,
                            "region": region, "price_tier": tier,
                            "total_hourly_usd": round(sp_price, 4),
                            "per_gpu_hourly_usd": round(sp_price / count, 4),
                        })
            except Exception:
                pass

    return {"provider": "Azure", "skus": skus}


# ─── PROVIDER 2: OCI ─────────────────────────────────────────────────────────
OCI_URL = "https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/"
OCI_GPU_KEYWORDS = [
    ("H100", "H100 SXM"), ("H200", "H200 SXM"), ("L40S", "L40S"),
    ("L4 ", "L4"), ("A10 ", "A10"), ("A100", "A100"),
    ("B100", "B100"), ("B200", "B200"), ("B300", "B300"),
]


def fetch_oci():
    try:
        data = requests.get(OCI_URL, timeout=60).json()
    except Exception as e:
        return {"provider": "OCI", "error": str(e), "skus": []}

    skus = []
    for item in data.get("items", []):
        name   = item.get("displayName", "")
        metric = item.get("metricName", "")
        if "GPU" not in metric.upper() or "GPU" not in name.upper():
            continue
        gpu_type = next((l for k, l in OCI_GPU_KEYWORDS if k in name), None)
        if not gpu_type:
            continue
        price = None
        for loc in item.get("currencyCodeLocalizations", []):
            if loc.get("currencyCode") == "USD":
                for p in loc.get("prices", []):
                    if p.get("model") == "PAY_AS_YOU_GO":
                        price = p.get("value")
        if not price:
            continue
        m = re.search(r"[.\s](\d+)\s*$", name.strip())
        count = int(m.group(1)) if m else 1
        skus.append({
            "part_number": item.get("partNumber"), "display_name": name,
            "gpu_type": gpu_type, "gpu_count": count,
            "region": "global", "price_tier": "on_demand",
            "per_gpu_hourly_usd": round(price, 4),
            "total_hourly_usd": round(price * count, 4),
        })
    return {"provider": "OCI", "skus": skus}


# ─── PROVIDER 3: AWS ─────────────────────────────────────────────────────────
AWS_INSTANCES = {
    "p5.48xlarge":        ("H100 SXM", 8),
    "p5e.48xlarge":       ("H200 SXM", 8),
    "p5en.48xlarge":      ("H200 SXM", 8),
    "p6-b200.48xlarge":   ("B200", 8),
    "p6e-gb200.36xlarge": ("B200", 4),
    "g6.xlarge":          ("L4", 1),
    "g6.4xlarge":         ("L4", 1),
    "g6.12xlarge":        ("L4", 4),
    "g6.48xlarge":        ("L4", 8),
    "g6e.xlarge":         ("L40S", 1),
    "g6e.4xlarge":        ("L40S", 1),
    "g6e.12xlarge":       ("L40S", 4),
    "g6e.48xlarge":       ("L40S", 8),
    "p4d.24xlarge":       ("A100 40GB", 8),
    "p4de.24xlarge":      ("A100 80GB", 8),
}


def fetch_aws():
    try:
        import boto3
        from botocore.exceptions import NoCredentialsError
    except ImportError:
        return {"provider": "AWS", "error": "boto3 not installed", "skus": []}
    try:
        client = boto3.client("pricing", region_name="us-east-1")
    except Exception as e:
        return {"provider": "AWS", "error": str(e), "skus": []}

    skus = []
    for region in REGIONS["aws"]:
        for inst, (gpu, count) in AWS_INSTANCES.items():
            filters = [
                {"Type": "TERM_MATCH", "Field": "instanceType",    "Value": inst},
                {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                {"Type": "TERM_MATCH", "Field": "tenancy",         "Value": "Shared"},
                {"Type": "TERM_MATCH", "Field": "preInstalledSw",  "Value": "NA"},
                {"Type": "TERM_MATCH", "Field": "capacitystatus",  "Value": "Used"},
                {"Type": "TERM_MATCH", "Field": "regionCode",      "Value": region},
            ]
            try:
                response = client.get_products(ServiceCode="AmazonEC2",
                                               Filters=filters, MaxResults=10)
            except NoCredentialsError:
                return {"provider": "AWS", "error": "No AWS credentials", "skus": []}
            except Exception:
                continue

            for ps in response.get("PriceList", []):
                obj = json.loads(ps)

                # On-demand
                for term in obj.get("terms", {}).get("OnDemand", {}).values():
                    for dim in term.get("priceDimensions", {}).values():
                        price = float(dim.get("pricePerUnit", {}).get("USD", 0))
                        if price > 0:
                            skus.append({
                                "instance_type": inst, "gpu_type": gpu,
                                "gpu_count": count, "region": region,
                                "price_tier": "on_demand",
                                "total_hourly_usd": round(price, 4),
                                "per_gpu_hourly_usd": round(price / count, 4),
                            })
                            break
                    break

                # Reserved — standard class, No Upfront, 1yr and 3yr
                for term_data in obj.get("terms", {}).get("Reserved", {}).values():
                    attrs = term_data.get("termAttributes", {})
                    if attrs.get("OfferingClass") != "standard":
                        continue
                    if attrs.get("PurchaseOption") != "No Upfront":
                        continue
                    lease = attrs.get("LeaseContractLength", "")
                    tier_name = {"1yr": "12mo", "3yr": "36mo"}.get(lease)
                    if not tier_name:
                        continue
                    for dim in term_data.get("priceDimensions", {}).values():
                        if "hrs" not in dim.get("unit", "").lower():
                            continue
                        price = float(dim.get("pricePerUnit", {}).get("USD", 0))
                        if price > 0:
                            skus.append({
                                "instance_type": inst, "gpu_type": gpu,
                                "gpu_count": count, "region": region,
                                "price_tier": tier_name,
                                "total_hourly_usd": round(price, 4),
                                "per_gpu_hourly_usd": round(price / count, 4),
                            })
                            break

    return {"provider": "AWS", "skus": skus}


# ─── PROVIDER 4: GCP ─────────────────────────────────────────────────────────
GCP_COMPUTE = "6F81-5844-456A"
GCP_GPU_PATTERNS = [
    ("H100", "H100 SXM"), ("H200", "H200 SXM"), ("L40S", "L40S"),
    ("Nvidia L4", "L4"), ("A100", "A100"), ("B200", "B200"), ("B300", "B300"),
]


def _gcp_tier(desc):
    d = desc.lower()
    if "spot preemptible" in d:
        return "spot"
    if "commitment v1" in d:
        return "36mo" if ("3 year" in d or "3 years" in d) else "12mo"
    if "dws defined duration" in d:
        return "12mo"
    if "calendar mode" in d:
        return "36mo"
    return "on_demand"


def fetch_gcp():
    api_key = os.environ.get("GCP_API_KEY")
    if not api_key:
        return {"provider": "GCP", "error": "GCP_API_KEY not set", "skus": []}

    url = f"https://cloudbilling.googleapis.com/v1/services/{GCP_COMPUTE}/skus"
    skus = []
    page_token = None
    target_regions = [INDIA_REGIONS["gcp"], US_REGIONS["gcp"]]
    pages = 0

    while pages < 30:
        params = {"key": api_key, "pageSize": 5000}
        if page_token:
            params["pageToken"] = page_token
        try:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return {"provider": "GCP", "error": str(e), "skus": skus}

        for sku in data.get("skus", []):
            desc = sku.get("description", "")
            cat  = sku.get("category", {})
            if "GPU" not in desc.upper() and "Gpu" not in str(cat.get("resourceGroup", "")):
                continue
            gpu_type = next((l for k, l in GCP_GPU_PATTERNS
                             if k.lower() in desc.lower()), None)
            if not gpu_type:
                continue
            regions  = sku.get("serviceRegions", [])
            matching = [r for r in regions if r in target_regions]
            if not matching:
                continue
            tier = _gcp_tier(desc)
            for pinfo in sku.get("pricingInfo", []):
                for t in pinfo.get("pricingExpression", {}).get("tieredRates", []):
                    up = t.get("unitPrice", {})
                    price = int(up.get("units", 0)) + up.get("nanos", 0) / 1e9
                    if price > 0:
                        for r in matching:
                            skus.append({
                                "description": desc, "gpu_type": gpu_type,
                                "region": r, "price_tier": tier,
                                "per_gpu_hourly_usd": round(price, 4),
                            })
                        break
                break

        page_token = data.get("nextPageToken")
        if not page_token:
            break
        pages += 1

    return {"provider": "GCP", "skus": skus}


# ─── PROVIDER 5: RUNPOD ──────────────────────────────────────────────────────
RUNPOD_QUERY = """
{
  gpuTypes {
    id displayName memoryInGb
    securePrice communityPrice
    oneMonthPrice threeMonthPrice sixMonthPrice
  }
}
"""
RUNPOD_KEYWORDS = [
    ("h100", "H100 SXM"), ("h200", "H200 SXM"), ("l40s", "L40S"),
    ("l40", "L40"), ("l4", "L4"), ("a100", "A100"),
    ("b300", "B300"), ("b200", "B200"),
]


def fetch_runpod():
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        return {"provider": "RunPod", "error": "RUNPOD_API_KEY not set", "skus": []}
    try:
        data = requests.post(f"https://api.runpod.io/graphql?api_key={api_key}",
                             json={"query": RUNPOD_QUERY}, timeout=30).json()
    except Exception as e:
        return {"provider": "RunPod", "error": str(e), "skus": []}

    if "errors" in data:
        return {"provider": "RunPod", "error": str(data["errors"])[:200], "skus": []}

    skus = []
    for gpu in data.get("data", {}).get("gpuTypes", []) or []:
        display   = (gpu.get("displayName") or "").lower()
        gpu_type  = next((l for k, l in RUNPOD_KEYWORDS if k in display), None)
        if not gpu_type:
            continue
        for tier, price in [
            ("on_demand", gpu.get("securePrice")),
            ("1mo",       gpu.get("oneMonthPrice")),
            ("6mo",       gpu.get("sixMonthPrice")),
            ("36mo",      gpu.get("threeMonthPrice")),
            ("spot",      gpu.get("communityPrice")),
        ]:
            if price:
                skus.append({
                    "id": gpu.get("id"), "display_name": gpu.get("displayName"),
                    "gpu_type": gpu_type, "memory_gb": gpu.get("memoryInGb"),
                    "region": "global", "price_tier": tier,
                    "per_gpu_hourly_usd": price,
                })
    return {"provider": "RunPod", "skus": skus}


# ─── RAW CSV ─────────────────────────────────────────────────────────────────
def write_raw_csv(output, path):
    rows = []
    for prov_key, prov_data in output["providers"].items():
        for sku in prov_data.get("skus", []):
            rows.append({
                "provider":           prov_data.get("provider", prov_key),
                "gpu_type":           sku.get("gpu_type"),
                "gpu_count":          sku.get("gpu_count", ""),
                "sku":                (sku.get("sku") or sku.get("instance_type")
                                       or sku.get("part_number") or sku.get("id")
                                       or sku.get("description") or sku.get("display_name") or ""),
                "price_tier":         sku.get("price_tier", "on_demand"),
                "per_gpu_hourly_usd": sku.get("per_gpu_hourly_usd") or "",
                "total_hourly_usd":   sku.get("total_hourly_usd", ""),
                "region":             sku.get("region") or "",
            })
    if not rows:
        return
    rows.sort(key=lambda r: (r["gpu_type"] or "", r["provider"],
                              r["price_tier"] or "", r["region"] or ""))
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


# ─── COMPARISON CSV ──────────────────────────────────────────────────────────
NEYSA_TIERS   = ["on_demand", "1mo", "6mo", "12mo", "24mo", "36mo"]
COMP_TIERS    = ["on_demand", "1mo", "6mo", "12mo", "36mo", "spot"]
PROVIDERS_ORDER = ["Azure", "AWS", "GCP", "OCI", "RunPod"]
TIER_LABELS   = {
    "on_demand": "On-demand (PAYG)",
    "1mo":  "1-month committed",
    "6mo":  "6-month committed",
    "12mo": "12-month / 1-yr reserved",
    "24mo": "24-month committed",
    "36mo": "36-month / 3-yr reserved",
    "spot": "Spot / Community Cloud",
}


def _best_price(skus, gpu_type, tier, india, us):
    candidates = [s for s in skus
                  if s.get("gpu_type") == gpu_type
                  and s.get("price_tier") == tier
                  and s.get("per_gpu_hourly_usd")]
    if not candidates:
        return None, None

    def pri(s):
        r = s.get("region", "")
        return 0 if r == india else 1 if r == us else 2 if r == "global" else 3

    candidates.sort(key=lambda s: (pri(s), float(s.get("per_gpu_hourly_usd", 999))))
    b = candidates[0]
    return b.get("per_gpu_hourly_usd"), b.get("region", "")


def write_comparison_csv(output, path):
    providers = {v.get("provider", k): v.get("skus", [])
                 for k, v in output["providers"].items()}

    neysa_cols = [f"neysa_{t}" for t in NEYSA_TIERS]
    comp_cols  = [f"{p.lower()}_{t}" for p in PROVIDERS_ORDER for t in COMP_TIERS]
    all_cols   = ["gpu_type", "pricing_note"] + neysa_cols + comp_cols

    guide = {
        "gpu_type": "COLUMN GUIDE",
        "pricing_note": "India pricing used where available (IN); US fallback (US); global = provider-wide",
        **{f"neysa_{t}": f"Neysa — {TIER_LABELS[t]}" for t in NEYSA_TIERS},
        **{f"{p.lower()}_{t}": f"{p} — {TIER_LABELS[t]}"
           for p in PROVIDERS_ORDER for t in COMP_TIERS},
    }

    rows = []
    for gpu in GPU_TYPES:
        neysa = NEYSA.get(gpu, {})
        row   = {"gpu_type": gpu, "pricing_note": "India preferred; US fallback"}
        for t in NEYSA_TIERS:
            v = neysa.get(t)
            row[f"neysa_{t}"] = f"${v:.2f}" if v else "—"
        for pname in PROVIDERS_ORDER:
            skus  = providers.get(pname, [])
            india = INDIA_REGIONS.get(pname.lower(), "")
            us    = US_REGIONS.get(pname.lower(), "")
            for t in COMP_TIERS:
                price, region = _best_price(skus, gpu, t, india, us)
                col = f"{pname.lower()}_{t}"
                if price:
                    tag = (" (IN)"     if region == india  else
                           " (US)"     if region == us     else
                           " (global)" if region == "global" else f" ({region})")
                    row[col] = f"${float(price):.2f}{tag}"
                else:
                    row[col] = "—"
        rows.append(row)

    # Drop columns that are all "—" to reduce noise
    populated = [c for c in comp_cols
                 if any(row.get(c, "—") != "—" for row in rows)]
    final = ["gpu_type", "pricing_note"] + neysa_cols + populated

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=final)
        writer.writeheader()
        writer.writerow({k: v for k, v in guide.items() if k in final})
        writer.writerows(rows)


# ─── MAIN ────────────────────────────────────────────────────────────────────
def print_summary(output):
    print("\n" + "=" * 70)
    print(f"  SUMMARY · {output['fetched_at']}")
    print("=" * 70)
    for k, v in output["providers"].items():
        name = v.get("provider", k)
        if v.get("error"):
            print(f"  ✗ {name:15s}  {v['error'][:55]}")
        else:
            print(f"  ✓ {name:15s}  {len(v.get('skus', []))} SKUs")
    print()


def main():
    output = {"fetched_at": datetime.now(timezone.utc).isoformat(), "providers": {}}

    for key, fn in [("azure", fetch_azure), ("oci", fetch_oci), ("aws", fetch_aws),
                    ("gcp", fetch_gcp), ("runpod", fetch_runpod)]:
        print(f"→ Fetching {key} ...")
        try:
            output["providers"][key] = fn()
        except Exception as e:
            output["providers"][key] = {"error": f"unhandled: {e}", "skus": []}

    with open("gpu_pricing.json", "w") as f:
        json.dump(output, f, indent=2)

    write_raw_csv(output, "gpu_pricing_raw.csv")
    write_comparison_csv(output, "gpu_pricing_comparison.csv")

    print_summary(output)
    for fname in ["gpu_pricing.json", "gpu_pricing_raw.csv", "gpu_pricing_comparison.csv"]:
        if os.path.exists(fname):
            print(f"  Wrote {fname} ({os.path.getsize(fname) // 1024} KB)")


if __name__ == "__main__":
    main()