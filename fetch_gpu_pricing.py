#!/usr/bin/env python3
"""
GPU Pricing Fetcher v6 — Neysa Competitive Intelligence

New vs v5:
- Vultr GPU plans added (no auth needed — fully public API)
- Vast.ai marketplace added (no auth needed — public search endpoint)
- Raw CSV column order fixed: gpu_type + provider are now columns A and B
  so they're always visible when scrolling right

Providers: Azure, OCI, AWS, GCP, RunPod, Vultr, Vast.ai
"""

import os, sys, csv, json, re, statistics
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
    "azure": ["eastus", "eastus2", "westus2", "southindia"],
    "aws":   ["us-east-1", "ap-south-1"],
    "gcp":   ["us-central1", "asia-south1"],
}


# ─── PROVIDER 1: AZURE ───────────────────────────────────────────────────────
AZURE_SKUS = {
    "Standard_ND96isr_H100_v5":   ("H100 SXM", 8),
    "Standard_NC40ads_H100_v5":   ("H100 NVL", 1),
    "Standard_NC80adis_H100_v5":  ("H100 NVL", 2),
    "Standard_ND96isr_H200_v5":   ("H200 SXM", 8),
    "Standard_NV36ads_L4_v5":     ("L4", 1),
    "Standard_NV36adms_L4_v5":    ("L4", 1),
    "Standard_NV72ads_L4_v5":     ("L4", 2),
    "Standard_NC24ads_A100_v4":   ("A100", 1),
    "Standard_NC48ads_A100_v4":   ("A100", 2),
    "Standard_NC96ads_A100_v4":   ("A100", 4),
    "Standard_ND96amsr_A100_v4":  ("A100", 8),
    "Standard_ND_GB200_v6":       ("B200", 4),
}


def fetch_azure():
    base    = "https://prices.azure.com/api/retail/prices"
    api_ver = {"api-version": "2023-01-01-preview"}
    skus    = []
    seen    = set()

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
                item  = items[0]
                total = item["retailPrice"]
                key   = (arm_sku, region, "on_demand")
                if key not in seen:
                    seen.add(key)
                    skus.append({"sku": arm_sku, "gpu_type": gpu, "gpu_count": count,
                                 "region": region, "price_tier": "on_demand",
                                 "total_hourly_usd": round(total, 4),
                                 "per_gpu_hourly_usd": round(total / count, 4)})
                for sp in item.get("savingsPlan", []):
                    term     = sp.get("term", "")
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
                        skus.append({"sku": arm_sku, "gpu_type": gpu, "gpu_count": count,
                                     "region": region, "price_tier": tier,
                                     "total_hourly_usd": round(sp_price, 4),
                                     "per_gpu_hourly_usd": round(sp_price / count, 4)})
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
        name, metric = item.get("displayName", ""), item.get("metricName", "")
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
        m     = re.search(r"[.\s](\d+)\s*$", name.strip())
        count = int(m.group(1)) if m else 1
        skus.append({"part_number": item.get("partNumber"), "display_name": name,
                     "gpu_type": gpu_type, "gpu_count": count, "region": "global",
                     "price_tier": "on_demand", "per_gpu_hourly_usd": round(price, 4),
                     "total_hourly_usd": round(price * count, 4)})
    return {"provider": "OCI", "skus": skus}


# ─── PROVIDER 3: AWS ─────────────────────────────────────────────────────────
AWS_INSTANCES = {
    "p5.48xlarge":        ("H100 SXM", 8),
    "p5e.48xlarge":       ("H200 SXM", 8),
    "p5en.48xlarge":      ("H200 SXM", 8),
    "p6-b200.48xlarge":   ("B200", 8),
    "p6e-gb200.36xlarge": ("B200", 4),
    "g6.xlarge":          ("L4", 1),  "g6.4xlarge":   ("L4", 1),
    "g6.12xlarge":        ("L4", 4),  "g6.48xlarge":  ("L4", 8),
    "g6e.xlarge":         ("L40S", 1), "g6e.4xlarge": ("L40S", 1),
    "g6e.12xlarge":       ("L40S", 4), "g6e.48xlarge":("L40S", 8),
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
                for term in obj.get("terms", {}).get("OnDemand", {}).values():
                    for dim in term.get("priceDimensions", {}).values():
                        price = float(dim.get("pricePerUnit", {}).get("USD", 0))
                        if price > 0:
                            skus.append({"instance_type": inst, "gpu_type": gpu,
                                         "gpu_count": count, "region": region,
                                         "price_tier": "on_demand",
                                         "total_hourly_usd": round(price, 4),
                                         "per_gpu_hourly_usd": round(price / count, 4)})
                            break
                    break
                for term_data in obj.get("terms", {}).get("Reserved", {}).values():
                    attrs = term_data.get("termAttributes", {})
                    if attrs.get("OfferingClass") != "standard" or attrs.get("PurchaseOption") != "No Upfront":
                        continue
                    lease     = attrs.get("LeaseContractLength", "")
                    tier_name = {"1yr": "12mo", "3yr": "36mo"}.get(lease)
                    if not tier_name:
                        continue
                    for dim in term_data.get("priceDimensions", {}).values():
                        if "hrs" not in dim.get("unit", "").lower():
                            continue
                        price = float(dim.get("pricePerUnit", {}).get("USD", 0))
                        if price > 0:
                            skus.append({"instance_type": inst, "gpu_type": gpu,
                                         "gpu_count": count, "region": region,
                                         "price_tier": tier_name,
                                         "total_hourly_usd": round(price, 4),
                                         "per_gpu_hourly_usd": round(price / count, 4)})
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
    url    = f"https://cloudbilling.googleapis.com/v1/services/{GCP_COMPUTE}/skus"
    skus   = []
    token  = None
    pages  = 0
    target = [INDIA_REGIONS["gcp"], US_REGIONS["gcp"]]
    while pages < 30:
        params = {"key": api_key, "pageSize": 5000}
        if token:
            params["pageToken"] = token
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
            gpu_type = next((l for k, l in GCP_GPU_PATTERNS if k.lower() in desc.lower()), None)
            if not gpu_type:
                continue
            matching = [r for r in sku.get("serviceRegions", []) if r in target]
            if not matching:
                continue
            tier = _gcp_tier(desc)
            for pinfo in sku.get("pricingInfo", []):
                for t in pinfo.get("pricingExpression", {}).get("tieredRates", []):
                    up    = t.get("unitPrice", {})
                    price = int(up.get("units", 0)) + up.get("nanos", 0) / 1e9
                    if price > 0:
                        for r in matching:
                            skus.append({"description": desc, "gpu_type": gpu_type,
                                         "region": r, "price_tier": tier,
                                         "per_gpu_hourly_usd": round(price, 4)})
                        break
                break
        token = data.get("nextPageToken")
        if not token:
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
        display  = (gpu.get("displayName") or "").lower()
        gpu_type = next((l for k, l in RUNPOD_KEYWORDS if k in display), None)
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
                skus.append({"id": gpu.get("id"), "display_name": gpu.get("displayName"),
                             "gpu_type": gpu_type, "memory_gb": gpu.get("memoryInGb"),
                             "region": "global", "price_tier": tier,
                             "per_gpu_hourly_usd": price})
    return {"provider": "RunPod", "skus": skus}


# ─── PROVIDER 6: VULTR ───────────────────────────────────────────────────────
# No auth needed — fully public endpoint
VULTR_GPU_KEYWORDS = [
    ("h100", "H100 SXM"), ("h200", "H200 SXM"), ("l40s", "L40S"),
    ("l4",   "L4"),        ("a100", "A100"),      ("b200", "B200"),
    ("a16",  "A16"),       ("a10",  "A10"),
]


def fetch_vultr():
    """Vultr Cloud GPU plans — public API, no credentials needed."""
    url = "https://api.vultr.com/v2/plans"
    skus = []
    cursor = None

    while True:
        try:
            params = {"type": "vcg", "per_page": 500}
            if cursor:
                params["cursor"] = cursor
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return {"provider": "Vultr", "error": str(e), "skus": skus}

        for plan in data.get("plans", []):
            gpu_raw  = (plan.get("gpu_type") or "").lower()
            gpu_type = next((l for k, l in VULTR_GPU_KEYWORDS if k in gpu_raw), None)
            if not gpu_type:
                continue

            monthly   = plan.get("monthly_cost", 0) or 0
            if monthly <= 0:
                continue

            hourly    = monthly / 720
            gpu_count = plan.get("gpu_count", 1) or 1
            locations = plan.get("locations", []) or []

            skus.append({
                "plan_id":           plan.get("id"),
                "gpu_type":          gpu_type,
                "gpu_type_raw":      plan.get("gpu_type"),
                "gpu_count":         gpu_count,
                "vcpus":             plan.get("vcpu_count"),
                "ram_gb":            round((plan.get("ram", 0) or 0) / 1024, 1),
                "region":            ",".join(locations[:5]),
                "price_tier":        "on_demand",
                "total_hourly_usd":  round(hourly, 4),
                "per_gpu_hourly_usd": round(hourly / gpu_count, 4),
            })

        cursor = (data.get("meta", {}).get("links", {}).get("next") or "").strip() or None
        if not cursor:
            break

    return {"provider": "Vultr", "skus": skus}


# ─── PROVIDER 7: VAST.AI ─────────────────────────────────────────────────────
# No auth needed — public marketplace search endpoint
VASTAI_GPU_KEYWORDS = [
    ("H100_SXM",  "H100 SXM"), ("H100 SXM",  "H100 SXM"),
    ("H200",      "H200 SXM"),
    ("L40S",      "L40S"),
    ("L4",        "L4"),
    ("A100",      "A100"),
    ("B200",      "B200"), ("B300", "B300"),
]


def fetch_vastai():
    """Vast.ai GPU marketplace — no auth. Returns median spot rates per GPU type."""
    base_url = "https://console.vast.ai/api/v0/bundles/"
    buckets  = {}  # gpu_type -> list of per-GPU $/hr prices

    # Query each GPU type we care about
    gpu_queries = [
        ("H100", "H100 SXM"), ("H200", "H200 SXM"),
        ("L40S", "L40S"), ("L4", "L4"),
        ("A100", "A100"), ("B200", "B200"), ("B300", "B300"),
    ]

    for search_term, our_gpu in gpu_queries:
        try:
            params = {
                "q": json.dumps({
                    "rentable":    {"eq": True},
                    "verified":    {"eq": True},
                    "gpu_name":    {"icontains": search_term},
                })
            }
            resp = requests.get(base_url, params=params, timeout=30)
            resp.raise_for_status()
            offers = resp.json().get("offers", [])

            for offer in offers:
                dph      = offer.get("dph_total", 0) or 0
                num_gpus = offer.get("num_gpus", 1) or 1
                if dph > 0:
                    per_gpu = dph / num_gpus
                    if our_gpu not in buckets:
                        buckets[our_gpu] = []
                    buckets[our_gpu].append(per_gpu)
        except Exception:
            pass

    skus = []
    for gpu_type, prices in buckets.items():
        if not prices:
            continue
        prices.sort()
        median_price = statistics.median(prices)
        low_price    = prices[0]

        skus.append({
            "gpu_type":           gpu_type,
            "region":             "global",
            "price_tier":         "spot",  # Vast.ai is marketplace/spot
            "per_gpu_hourly_usd": round(median_price, 4),
            "note":               f"marketplace median of {len(prices)} offers",
        })
        # Also record the lowest available (interruptible)
        if low_price != median_price:
            skus.append({
                "gpu_type":           gpu_type,
                "region":             "global",
                "price_tier":         "spot_low",
                "per_gpu_hourly_usd": round(low_price, 4),
                "note":               "lowest available offer (unverified reliability)",
            })

    return {"provider": "Vast.ai", "skus": skus}


# ─── RAW CSV ─────────────────────────────────────────────────────────────────
# FIX: gpu_type and provider are now columns A and B — always visible when scrolling right
RAW_FIELDNAMES = [
    "gpu_type", "provider", "price_tier",
    "per_gpu_hourly_usd", "total_hourly_usd",
    "region", "gpu_count", "sku",
]


def write_raw_csv(output, path):
    rows = []
    for prov_key, prov_data in output["providers"].items():
        provider_name = prov_data.get("provider", prov_key)
        for sku in prov_data.get("skus", []):
            rows.append({
                "gpu_type":           sku.get("gpu_type", ""),
                "provider":           provider_name,
                "price_tier":         sku.get("price_tier", "on_demand"),
                "per_gpu_hourly_usd": sku.get("per_gpu_hourly_usd") or "",
                "total_hourly_usd":   sku.get("total_hourly_usd", ""),
                "region":             sku.get("region") or "",
                "gpu_count":          sku.get("gpu_count", ""),
                "sku":                (sku.get("sku") or sku.get("instance_type")
                                       or sku.get("plan_id") or sku.get("part_number")
                                       or sku.get("id") or sku.get("description")
                                       or sku.get("display_name") or sku.get("note") or ""),
            })
    if not rows:
        return
    # Sort by GPU type then provider for easy scanning
    rows.sort(key=lambda r: (r["gpu_type"] or "", r["provider"], r["price_tier"] or ""))
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RAW_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


# ─── COMPARISON CSV ──────────────────────────────────────────────────────────
NEYSA_TIERS     = ["on_demand", "1mo", "6mo", "12mo", "24mo", "36mo"]
COMP_TIERS      = ["on_demand", "1mo", "6mo", "12mo", "36mo", "spot"]
PROVIDERS_ORDER = ["Azure", "AWS", "GCP", "OCI", "RunPod", "Vultr", "Vast.ai"]
TIER_LABELS     = {
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
    comp_cols  = [f"{p.lower().replace('.', '')}_{t}"
                  for p in PROVIDERS_ORDER for t in COMP_TIERS]
    all_cols   = ["gpu_type", "pricing_note"] + neysa_cols + comp_cols

    guide = {
        "gpu_type":    "COLUMN GUIDE",
        "pricing_note":"India pricing used where available (IN); US fallback (US); global = provider-wide",
        **{f"neysa_{t}": f"Neysa — {TIER_LABELS[t]}" for t in NEYSA_TIERS},
        **{f"{p.lower().replace('.', '')}_{t}": f"{p} — {TIER_LABELS[t]}"
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
            col_key = pname.lower().replace(".", "")
            for t in COMP_TIERS:
                price, region = _best_price(skus, gpu, t, india, us)
                col = f"{col_key}_{t}"
                if price:
                    tag = (" (IN)"     if region == india   else
                           " (US)"     if region == us      else
                           " (global)" if region == "global" else f" ({region})")
                    row[col] = f"${float(price):.2f}{tag}"
                else:
                    row[col] = "—"
        rows.append(row)

    # Drop all-empty columns
    populated = [c for c in comp_cols
                 if any(row.get(c, "—") != "—" for row in rows)]
    final = ["gpu_type", "pricing_note"] + neysa_cols + populated

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=final, extrasaction='ignore')
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
    for key, fn in [
        ("azure",   fetch_azure),
        ("oci",     fetch_oci),
        ("aws",     fetch_aws),
        ("gcp",     fetch_gcp),
        ("runpod",  fetch_runpod),
        ("vultr",   fetch_vultr),
        ("vastai",  fetch_vastai),
    ]:
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