#!/usr/bin/env python3
"""
GPU Pricing Fetcher v2 — Neysa Competitive Intelligence

What's new vs v1:
- Queries both US and Indian regions for Azure / AWS / GCP
- Expanded SKU coverage: L4, L40S, H100 SXM, H100 NVL, H200 SXM, A100, B200, B300
- Better OCI parsing: only keeps actual GPU-per-hour rates, drops OCPU/memory companions
- More RunPod GPU types matched

Setup unchanged from v1:
  pip install requests boto3
  # set whichever keys you have:
  export GCP_API_KEY="..."
  export DO_API_TOKEN="..."
  export RUNPOD_API_KEY="..."
  # AWS: aws configure  or  AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY

Run:  python fetch_gpu_pricing.py
"""

import os, sys, csv, json, re
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run: pip install requests boto3")


# ─── REGION CONFIG ──────────────────────────────────────────────────────────
# Each provider queries these regions. Indian regions added for Neysa context.
REGIONS = {
    "azure": ["eastus", "southindia"],        # US East + Mumbai
    "aws":   ["us-east-1", "ap-south-1"],     # US East + Mumbai
    "gcp":   ["us-central1", "asia-south1"],  # Iowa + Mumbai
}
# Note: OCI publishes globally uniform pricing — single fetch covers all regions.
# Note: DigitalOcean and RunPod return per-SKU region lists; we include them as-is.
# Note: Newer GPUs (H200, B300) may not be deployed to Indian regions yet —
#       expect empty rows for those combos.


# ─── PROVIDER 1: AZURE ──────────────────────────────────────────────────────
# Hand-curated Azure GPU SKUs. Add more here as Azure releases them.
AZURE_SKUS = {
    # H100 SXM (8-GPU ND series)
    "Standard_ND96isr_H100_v5":   ("H100 SXM", 8),
    # H100 NVL (PCIe, NC series)
    "Standard_NC40ads_H100_v5":   ("H100 NVL", 1),
    "Standard_NC80adis_H100_v5":  ("H100 NVL", 2),
    # H200 SXM
    "Standard_ND96isr_H200_v5":   ("H200 SXM", 8),
    # L4 (NV-series)
    "Standard_NV6ads_L4_v5":      ("L4", 1),
    "Standard_NV12ads_L4_v5":     ("L4", 1),
    "Standard_NV18ads_L4_v5":     ("L4", 1),
    "Standard_NV36ads_L4_v5":     ("L4", 1),
    "Standard_NV72ads_L4_v5":     ("L4", 2),
    # A100 (older, useful baseline)
    "Standard_NC24ads_A100_v4":   ("A100", 1),
    "Standard_NC48ads_A100_v4":   ("A100", 2),
    "Standard_NC96ads_A100_v4":   ("A100", 4),
    "Standard_ND96amsr_A100_v4":  ("A100", 8),
    # Speculative — will skip silently if SKU doesn't exist in a region:
    "Standard_ND_GB200_v6":       ("B200", 4),
}


def fetch_azure():
    base = "https://prices.azure.com/api/retail/prices"
    skus = []
    for region in REGIONS["azure"]:
        for arm_sku, (gpu, count) in AZURE_SKUS.items():
            flt = (
                f"serviceName eq 'Virtual Machines' "
                f"and armSkuName eq '{arm_sku}' "
                f"and armRegionName eq '{region}' "
                f"and priceType eq 'Consumption'"
            )
            try:
                resp = requests.get(base, params={"$filter": flt}, timeout=30)
                resp.raise_for_status()
                items = resp.json().get("Items", [])
            except requests.RequestException:
                continue

            items = [i for i in items
                     if "Windows" not in i.get("productName", "")
                     and "Spot" not in i.get("meterName", "")
                     and "Low Priority" not in i.get("meterName", "")]
            if not items:
                continue

            items.sort(key=lambda x: x.get("retailPrice", float("inf")))
            cheap = items[0]
            total = cheap["retailPrice"]
            skus.append({
                "sku": arm_sku,
                "gpu_type": gpu,
                "gpu_count": count,
                "region": cheap["armRegionName"],
                "total_hourly_usd": round(total, 4),
                "per_gpu_hourly_usd": round(total / count, 4),
            })
    return {"provider": "Azure", "skus": skus}


# ─── PROVIDER 2: OCI ────────────────────────────────────────────────────────
OCI_URL = "https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/"

OCI_GPU_KEYWORDS = [
    ("H100", "H100 SXM"),
    ("H200", "H200 SXM"),
    ("L40S", "L40S"),
    ("L4 ",  "L4"),
    ("A10 ", "A10"),
    ("A100", "A100"),
    ("B100", "B100"),
    ("B200", "B200"),
    ("B300", "B300"),
]


def fetch_oci():
    try:
        resp = requests.get(OCI_URL, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return {"provider": "OCI", "error": str(e), "skus": []}

    skus = []
    for item in data.get("items", []):
        name = item.get("displayName", "")
        metric = item.get("metricName", "")

        # CRITICAL: only keep GPU-priced line items.
        # OCI bundles GPU shapes with OCPU + Memory companions priced separately;
        # we only want the actual GPU rate.
        if "GPU" not in metric.upper():
            continue
        if "GPU" not in name.upper():
            continue

        gpu_type = next((label for kw, label in OCI_GPU_KEYWORDS if kw in name), None)
        if not gpu_type:
            continue

        price = None
        for loc in item.get("currencyCodeLocalizations", []):
            if loc.get("currencyCode") == "USD":
                for p in loc.get("prices", []):
                    if p.get("model") == "PAY_AS_YOU_GO":
                        price = p.get("value")
                        break
        if not price:
            continue

        # OCI's "GPU Per Hour" metric is already per-individual-GPU.
        # Display name may include shape count like "...H100.8"; capture if present.
        count_match = re.search(r"[.\s](\d+)\s*$", name.strip())
        shape_count = int(count_match.group(1)) if count_match else 1

        skus.append({
            "part_number": item.get("partNumber"),
            "display_name": name,
            "gpu_type": gpu_type,
            "gpu_count": shape_count,
            "region": "global",
            "metric": metric,
            "per_gpu_hourly_usd": round(price, 4),
            "total_hourly_usd": round(price * shape_count, 4),
        })
    return {"provider": "OCI", "skus": skus}


# ─── PROVIDER 3: AWS ────────────────────────────────────────────────────────
AWS_INSTANCES = {
    # H100 SXM
    "p5.48xlarge":         ("H100 SXM", 8),
    # H200 SXM
    "p5e.48xlarge":        ("H200 SXM", 8),
    "p5en.48xlarge":       ("H200 SXM", 8),
    # B200 (Blackwell) — limited regions
    "p6-b200.48xlarge":    ("B200", 8),
    "p6e-gb200.36xlarge":  ("B200", 4),
    # L4
    "g6.xlarge":           ("L4", 1),
    "g6.4xlarge":          ("L4", 1),
    "g6.12xlarge":         ("L4", 4),
    "g6.48xlarge":         ("L4", 8),
    # L40S
    "g6e.xlarge":          ("L40S", 1),
    "g6e.4xlarge":         ("L40S", 1),
    "g6e.12xlarge":        ("L40S", 4),
    "g6e.48xlarge":        ("L40S", 8),
    # A100 (baseline comparison)
    "p4d.24xlarge":        ("A100 40GB", 8),
    "p4de.24xlarge":       ("A100 80GB", 8),
}


def fetch_aws():
    try:
        import boto3
        from botocore.exceptions import NoCredentialsError, ClientError
    except ImportError:
        return {"provider": "AWS", "error": "boto3 not installed", "skus": []}

    try:
        client = boto3.client("pricing", region_name="us-east-1")  # API endpoint only here
    except Exception as e:
        return {"provider": "AWS", "error": f"client init: {e}", "skus": []}

    skus = []
    for region in REGIONS["aws"]:
        for inst, (gpu, count) in AWS_INSTANCES.items():
            try:
                response = client.get_products(
                    ServiceCode="AmazonEC2",
                    Filters=[
                        {"Type": "TERM_MATCH", "Field": "instanceType",    "Value": inst},
                        {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                        {"Type": "TERM_MATCH", "Field": "tenancy",         "Value": "Shared"},
                        {"Type": "TERM_MATCH", "Field": "preInstalledSw",  "Value": "NA"},
                        {"Type": "TERM_MATCH", "Field": "capacitystatus",  "Value": "Used"},
                        {"Type": "TERM_MATCH", "Field": "regionCode",      "Value": region},
                    ],
                    MaxResults=5,
                )
            except NoCredentialsError:
                return {"provider": "AWS", "error": "No AWS credentials. Run `aws configure`", "skus": []}
            except ClientError:
                continue

            for ps in response.get("PriceList", []):
                obj = json.loads(ps)
                done = False
                for term in obj.get("terms", {}).get("OnDemand", {}).values():
                    for dim in term.get("priceDimensions", {}).values():
                        price = float(dim.get("pricePerUnit", {}).get("USD", 0))
                        if price > 0:
                            skus.append({
                                "instance_type": inst,
                                "gpu_type": gpu,
                                "gpu_count": count,
                                "region": region,
                                "total_hourly_usd": round(price, 4),
                                "per_gpu_hourly_usd": round(price / count, 4),
                            })
                            done = True
                            break
                    if done: break
                if done: break  # one price per instance × region
    return {"provider": "AWS", "skus": skus}


# ─── PROVIDER 4: GCP ────────────────────────────────────────────────────────
GCP_COMPUTE_SERVICE = "6F81-5844-456A"
GCP_GPU_PATTERNS = [
    ("H100",      "H100 SXM"),
    ("H200",      "H200 SXM"),
    ("L40S",      "L40S"),
    ("Nvidia L4", "L4"),
    ("A100",      "A100"),
    ("B200",      "B200"),
    ("B300",      "B300"),
]


def fetch_gcp():
    api_key = os.environ.get("GCP_API_KEY")
    if not api_key:
        return {"provider": "GCP",
                "error": "GCP_API_KEY env var not set",
                "skus": []}

    url = f"https://cloudbilling.googleapis.com/v1/services/{GCP_COMPUTE_SERVICE}/skus"
    skus = []
    page_token = None
    target_regions = REGIONS["gcp"]
    pages = 0

    while pages < 30:
        params = {"key": api_key, "pageSize": 5000}
        if page_token:
            params["pageToken"] = page_token
        try:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            return {"provider": "GCP", "error": str(e), "skus": skus}

        for sku in data.get("skus", []):
            desc = sku.get("description", "")
            cat = sku.get("category", {})

            if "GPU" not in desc.upper() and "Gpu" not in str(cat.get("resourceGroup", "")):
                continue

            gpu_type = next((label for kw, label in GCP_GPU_PATTERNS
                             if kw.lower() in desc.lower()), None)
            if not gpu_type:
                continue

            if cat.get("usageType", "") != "OnDemand":
                continue

            regions = sku.get("serviceRegions", [])
            matching = [r for r in regions if r in target_regions]
            if not matching:
                continue

            for pinfo in sku.get("pricingInfo", []):
                for tier in pinfo.get("pricingExpression", {}).get("tieredRates", []):
                    up = tier.get("unitPrice", {})
                    price = int(up.get("units", 0)) + up.get("nanos", 0) / 1e9
                    if price > 0:
                        for r in matching:
                            skus.append({
                                "description": desc,
                                "gpu_type": gpu_type,
                                "region": r,
                                "per_gpu_hourly_usd": round(price, 4),
                            })
                        break
                break

        page_token = data.get("nextPageToken")
        if not page_token:
            break
        pages += 1

    return {"provider": "GCP", "skus": skus}


# ─── PROVIDER 5: DIGITALOCEAN ───────────────────────────────────────────────
def fetch_digitalocean():
    token = os.environ.get("DO_API_TOKEN")
    if not token:
        return {"provider": "DigitalOcean",
                "error": "DO_API_TOKEN env var not set",
                "skus": []}

    url = "https://api.digitalocean.com/v2/sizes"
    headers = {"Authorization": f"Bearer {token}"}
    skus = []
    page = 1

    while page < 10:
        try:
            resp = requests.get(url, headers=headers,
                                params={"page": page, "per_page": 200}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            return {"provider": "DigitalOcean", "error": str(e), "skus": skus}

        for size in data.get("sizes", []):
            slug = size.get("slug", "")
            if not slug.startswith("gpu-"):
                continue

            sl = slug.lower()
            if   "h100" in sl:    gpu_type = "H100 SXM"
            elif "h200" in sl:    gpu_type = "H200 SXM"
            elif "l40s" in sl:    gpu_type = "L40S"
            elif "b300" in sl:    gpu_type = "B300"
            elif "b200" in sl:    gpu_type = "B200"
            elif "rtx4000" in sl: gpu_type = "RTX 4000"
            elif "rtx6000" in sl: gpu_type = "RTX 6000"
            elif "mi300x" in sl:  gpu_type = "MI300X"
            elif "mi325x" in sl:  gpu_type = "MI325X"
            else: continue

            m = re.search(r"x(\d+)", slug)
            count = int(m.group(1)) if m else 1
            hourly = size.get("price_hourly", 0) or 0
            if hourly > 0:
                skus.append({
                    "slug": slug,
                    "gpu_type": gpu_type,
                    "gpu_count": count,
                    "region": ",".join(size.get("regions", []) or []),
                    "total_hourly_usd": round(hourly, 4),
                    "per_gpu_hourly_usd": round(hourly / count, 4),
                })

        if not data.get("links", {}).get("pages", {}).get("next"):
            break
        page += 1
    return {"provider": "DigitalOcean", "skus": skus}


# ─── PROVIDER 6: RUNPOD ─────────────────────────────────────────────────────
RUNPOD_QUERY = """
{
  gpuTypes {
    id
    displayName
    memoryInGb
    secureCloud
    communityCloud
    securePrice
    communityPrice
    oneMonthPrice
    threeMonthPrice
    sixMonthPrice
  }
}
"""

RUNPOD_KEYWORDS = [
    ("h100", "H100 SXM"),
    ("h200", "H200 SXM"),
    ("l40s", "L40S"),
    ("l40",  "L40"),
    ("l4",   "L4"),
    ("a100", "A100"),
    ("b300", "B300"),
    ("b200", "B200"),
    ("b100", "B100"),
]


def fetch_runpod():
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        return {"provider": "RunPod",
                "error": "RUNPOD_API_KEY env var not set",
                "skus": []}

    try:
        resp = requests.post(
            f"https://api.runpod.io/graphql?api_key={api_key}",
            json={"query": RUNPOD_QUERY}, timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return {"provider": "RunPod", "error": str(e), "skus": []}

    if "errors" in data:
        return {"provider": "RunPod",
                "error": json.dumps(data["errors"])[:200],
                "skus": []}

    skus = []
    for gpu in data.get("data", {}).get("gpuTypes", []) or []:
        display = (gpu.get("displayName") or "").lower()
        gpu_type = next((label for kw, label in RUNPOD_KEYWORDS if kw in display), None)
        if not gpu_type:
            continue
        skus.append({
            "id": gpu.get("id"),
            "display_name": gpu.get("displayName"),
            "gpu_type": gpu_type,
            "memory_gb": gpu.get("memoryInGb"),
            "per_gpu_hourly_usd": gpu.get("securePrice"),
            "secure_per_gpu_hourly_usd": gpu.get("securePrice"),
            "community_per_gpu_hourly_usd": gpu.get("communityPrice"),
            "one_month_per_gpu_hourly_usd": gpu.get("oneMonthPrice"),
            "three_month_per_gpu_hourly_usd": gpu.get("threeMonthPrice"),
            "six_month_per_gpu_hourly_usd": gpu.get("sixMonthPrice"),
        })
    return {"provider": "RunPod", "skus": skus}


# ─── OUTPUT ─────────────────────────────────────────────────────────────────
def write_csv(output, path):
    rows = []
    for prov_key, prov_data in output["providers"].items():
        for sku in prov_data.get("skus", []):
            rows.append({
                "provider": prov_data.get("provider", prov_key),
                "gpu_type": sku.get("gpu_type"),
                "gpu_count": sku.get("gpu_count", ""),
                "sku":      sku.get("sku") or sku.get("instance_type")
                            or sku.get("slug") or sku.get("part_number")
                            or sku.get("id") or sku.get("description") or "",
                "per_gpu_hourly_usd": sku.get("per_gpu_hourly_usd") or "",
                "total_hourly_usd":   sku.get("total_hourly_usd", ""),
                "region": sku.get("region") or "",
            })
    if not rows:
        return
    rows.sort(key=lambda r: (
        r["gpu_type"] or "",
        r["region"] or "",
        r["provider"],
        float(r["per_gpu_hourly_usd"]) if r["per_gpu_hourly_usd"] not in ("", None) else 0,
    ))
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def print_summary(output):
    print("\n" + "=" * 70)
    print(f"  SUMMARY · fetched {output['fetched_at']}")
    print("=" * 70)
    for prov_key, prov_data in output["providers"].items():
        name = prov_data.get("provider", prov_key)
        if prov_data.get("error"):
            print(f"  ✗ {name:15s}  skipped — {prov_data['error'][:55]}")
        else:
            n = len(prov_data.get("skus", []))
            print(f"  ✓ {name:15s}  {n} SKU{'s' if n != 1 else ''}")
    print()


def main():
    output = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "providers": {},
    }
    fetchers = [
        ("azure",         fetch_azure),
        ("oci",           fetch_oci),
        ("aws",           fetch_aws),
        ("gcp",           fetch_gcp),
        ("digitalocean",  fetch_digitalocean),
        ("runpod",        fetch_runpod),
    ]
    for key, fn in fetchers:
        print(f"→ Fetching {key} ...")
        try:
            output["providers"][key] = fn()
        except Exception as e:
            output["providers"][key] = {"error": f"unhandled: {e}", "skus": []}

    with open("gpu_pricing.json", "w") as f:
        json.dump(output, f, indent=2)
    write_csv(output, "gpu_pricing.csv")

    print_summary(output)
    print(f"  Wrote gpu_pricing.json ({os.path.getsize('gpu_pricing.json')//1024} KB)")
    if os.path.exists("gpu_pricing.csv"):
        print(f"  Wrote gpu_pricing.csv  ({os.path.getsize('gpu_pricing.csv')//1024} KB)")


if __name__ == "__main__":
    main()
