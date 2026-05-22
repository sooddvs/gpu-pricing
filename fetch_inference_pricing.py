#!/usr/bin/env python3
"""
Inference API Pricing Fetcher — Neysa Competitive Intelligence

Fetches live per-token pricing from:
  • AWS Bedrock       — all models (Llama, Mistral, Claude, Nova, DeepSeek, etc.)
  • Azure OpenAI      — GPT-4o, o1, o3, GPT-4.1, GPT-5, etc.
  • Azure AI Foundry  — Llama, Mistral, DeepSeek, Phi, etc. (marketplace models)
  • GCP Vertex AI     — Gemini 2.x, 2.5, 3.x and all other models

Outputs: inference_api_pricing.csv
  Columns: provider, model, input_per_1m_usd, output_per_1m_usd,
           blended_per_1m_usd, region, source, fetched_at

Credentials needed:
  • AWS: AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (already configured)
  • Azure: none — fully public API
  • GCP: GCP_API_KEY env var (already configured)

Provisions for future models: queries are broad — no model-name filters.
New models added by each provider are picked up automatically on the next run.

Run after fetch_gpu_pricing.py:
    python fetch_inference_pricing.py
"""

import os, sys, csv, json, re
from datetime import datetime, timezone
from collections import defaultdict

try:
    import requests
except ImportError:
    sys.exit("Run: pip install requests boto3")


INFERENCE_FILE = "inference_api_pricing.csv"
FIELDNAMES = [
    "provider", "model", "token_type",
    "price_per_1m_usd", "region", "raw_unit", "source", "fetched_at",
]

TODAY = datetime.now(timezone.utc).isoformat()

# Regions to query for pricing
AWS_REGIONS   = ["us-east-1", "ap-south-1"]
AZURE_REGIONS = ["eastus", "eastus2", "southindia"]
GCP_REGIONS   = ["us-central1", "asia-south1"]

# GCP Gemini API service ID (confirmed from GCP SKU catalog)
GCP_GEMINI_SERVICE_ID = "AEFD-7695-64FA"
# GCP Vertex AI service ID (for non-Gemini models)
GCP_VERTEX_SERVICE_ID = "C1CA-19E8-4A4E"


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def to_per_1m(price, unit):
    """Normalise any token price unit to USD per 1M tokens."""
    if not price or price == 0:
        return None
    unit_lower = (unit or "").lower()
    if "1k" in unit_lower or "1,000" in unit_lower:
        return round(float(price) * 1_000, 6)
    if "1m" in unit_lower or "1,000,000" in unit_lower:
        return round(float(price), 6)
    if "token" in unit_lower and "k" not in unit_lower and "m" not in unit_lower:
        # price per single token
        return round(float(price) * 1_000_000, 6)
    # Default: assume per-token
    return round(float(price) * 1_000_000, 6)


def normalize_model_name(raw):
    """Light cleanup of model names for readability."""
    if not raw:
        return raw
    # Remove region prefixes like "US East (N. Virginia): "
    raw = re.sub(r"^[A-Z][A-Z]\s+\w.*?\):\s*", "", raw)
    return raw.strip()


# ─── PROVIDER 1: AWS BEDROCK ─────────────────────────────────────────────────

def fetch_bedrock():
    """
    AWS Bedrock — uses the AWS Pricing API (ServiceCode: AmazonBedrock).
    Returns all on-demand inference models found in specified regions.
    Credentials: existing AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY.
    """
    try:
        import boto3
        from botocore.exceptions import NoCredentialsError, ClientError
    except ImportError:
        return {"provider": "AWS Bedrock", "error": "boto3 not installed", "skus": []}

    try:
        client = boto3.client("pricing", region_name="us-east-1")
    except Exception as e:
        return {"provider": "AWS Bedrock", "error": str(e), "skus": []}

    skus = []
    for region in AWS_REGIONS:
        # Fetch all on-demand inference products in this region
        # Note: AWS Bedrock pricing has a known inconsistency where some providers'
        # output tokens may require separate queries. We query broadly and handle gaps.
        try:
            paginator = client.get_paginator("get_products")
            pages = paginator.paginate(
                ServiceCode="AmazonBedrock",
                Filters=[
                    {"Type": "TERM_MATCH", "Field": "feature",    "Value": "On-demand Inference"},
                    {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
                ],
            )
            for page in pages:
                for price_str in page.get("PriceList", []):
                    obj  = json.loads(price_str)
                    attr = obj.get("product", {}).get("attributes", {})
                    model     = attr.get("model") or attr.get("modelId") or ""
                    provider  = attr.get("provider", "")
                    inf_type  = attr.get("inferenceType", "").lower()
                    usagetype = attr.get("usagetype", "").lower()

                    # Determine input vs output from inferenceType or usagetype
                    if "input" in inf_type or "input" in usagetype:
                        token_type = "input"
                    elif "output" in inf_type or "output" in usagetype:
                        token_type = "output"
                    else:
                        token_type = "combined"

                    # Skip batch/cache pricing — on-demand only
                    if "batch" in usagetype or "cache" in usagetype:
                        continue

                    for term in obj.get("terms", {}).get("OnDemand", {}).values():
                        for dim in term.get("priceDimensions", {}).values():
                            price = float(dim.get("pricePerUnit", {}).get("USD", 0) or 0)
                            unit  = dim.get("unit", "")
                            if price > 0:
                                per_1m = to_per_1m(price, unit)
                                if per_1m:
                                    skus.append({
                                        "provider":        "AWS Bedrock",
                                        "model":           normalize_model_name(
                                                               f"{provider} {model}".strip()),
                                        "token_type":      token_type,
                                        "price_per_1m_usd": per_1m,
                                        "region":          region,
                                        "raw_unit":        unit,
                                        "source":          "AWS Pricing API",
                                        "fetched_at":      TODAY,
                                    })
                                    break
                        break

        except NoCredentialsError:
            return {"provider": "AWS Bedrock", "error": "No AWS credentials configured", "skus": []}
        except ClientError as e:
            return {"provider": "AWS Bedrock", "error": str(e), "skus": []}
        except Exception as e:
            return {"provider": "AWS Bedrock", "error": f"unhandled: {e}", "skus": []}

    return {"provider": "AWS Bedrock", "skus": skus}


# ─── PROVIDER 2: AZURE OPENAI ────────────────────────────────────────────────

AZURE_INFERENCE_SERVICES = [
    "Azure OpenAI",
    "Azure AI Model Inference",  # Azure AI Foundry serverless models
]


def fetch_azure_inference():
    """
    Azure Retail Prices API — no auth needed (fully public).
    Queries Azure OpenAI and Azure AI Foundry (Model-as-a-Service) pricing.
    Returns all token-based pricing found across configured regions.
    """
    base = "https://prices.azure.com/api/retail/prices"
    skus = []

    for svc_name in AZURE_INFERENCE_SERVICES:
        for region in AZURE_REGIONS:
            flt = (f"serviceName eq '{svc_name}' "
                   f"and armRegionName eq '{region}' "
                   f"and priceType eq 'Consumption'")
            try:
                url = base
                while url:
                    resp = requests.get(url if url.startswith("http") else base,
                                        params={"$filter": flt} if url == base else None,
                                        timeout=30)
                    resp.raise_for_status()
                    data = resp.json()

                    for item in data.get("Items", []):
                        product = item.get("productName", "")
                        meter   = item.get("meterName",   "")
                        price   = item.get("retailPrice",  0)
                        unit    = item.get("unitOfMeasure", "")

                        # Only token-based pricing
                        if not any(t in unit.lower() for t in ["token", "1k", "1m"]):
                            continue
                        if price <= 0:
                            continue

                        # Determine input vs output from meterName
                        meter_lower = meter.lower()
                        if "input" in meter_lower or " in " in meter_lower or " in-" in meter_lower:
                            token_type = "input"
                        elif "output" in meter_lower or " out " in meter_lower or " out-" in meter_lower:
                            token_type = "output"
                        elif "cache" in meter_lower:
                            token_type = "cached_input"
                        else:
                            token_type = "combined"

                        # Extract model name: strip service prefix from productName
                        model = product
                        for prefix in ["Azure OpenAI Service - ", "Azure OpenAI - ",
                                       "Azure AI Model Inference - "]:
                            model = model.replace(prefix, "")

                        per_1m = to_per_1m(price, unit)
                        if per_1m:
                            skus.append({
                                "provider":         svc_name,
                                "model":            model.strip(),
                                "token_type":       token_type,
                                "price_per_1m_usd": per_1m,
                                "region":           item.get("armRegionName", region),
                                "raw_unit":         unit,
                                "source":           "Azure Retail Prices API",
                                "fetched_at":       TODAY,
                            })

                    # Handle pagination
                    next_page = data.get("NextPageLink")
                    url = next_page if next_page else None

            except Exception:
                pass

    return {"provider": "Azure AI Services", "skus": skus}


# ─── PROVIDER 3: GCP VERTEX AI / GEMINI ──────────────────────────────────────

# Service IDs to query: Gemini API is confirmed, Vertex AI for other models
GCP_AI_SERVICES = [
    ("AEFD-7695-64FA", "GCP Gemini API"),
    ("C1CA-19E8-4A4E", "GCP Vertex AI"),  # May have other AI models
]


def fetch_vertex():
    """
    GCP Cloud Billing API — requires GCP_API_KEY env var.
    Queries the Gemini API service (AEFD-7695-64FA) for all token pricing.
    Auto-discovers new models as they're added to the service.
    """
    api_key = os.environ.get("GCP_API_KEY")
    if not api_key:
        return {"provider": "GCP", "error": "GCP_API_KEY not set", "skus": []}

    skus = []

    for service_id, provider_name in GCP_AI_SERVICES:
        url   = f"https://cloudbilling.googleapis.com/v1/services/{service_id}/skus"
        token = None
        pages = 0

        while pages < 20:
            params = {"key": api_key, "pageSize": 5000}
            if token:
                params["pageToken"] = token

            try:
                resp = requests.get(url, params=params, timeout=60)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                break

            for sku in data.get("skus", []):
                desc = sku.get("description", "")

                # Only token-based SKUs
                desc_lower = desc.lower()
                if "token" not in desc_lower and "character" not in desc_lower:
                    continue

                # Determine input vs output
                if "input" in desc_lower:
                    token_type = "input"
                elif "output" in desc_lower:
                    token_type = "output"
                else:
                    continue

                # Filter to target regions
                regions = sku.get("serviceRegions", [])
                matched_regions = [r for r in GCP_REGIONS if r in regions]
                if not matched_regions and regions:
                    # Use first available region if target not found
                    matched_regions = [regions[0]]
                elif not regions:
                    matched_regions = ["global"]

                # Extract price
                for pinfo in sku.get("pricingInfo", []):
                    for tier in pinfo.get("pricingExpression", {}).get("tieredRates", []):
                        up    = tier.get("unitPrice", {})
                        price = int(up.get("units", 0)) + up.get("nanos", 0) / 1e9
                        unit  = pinfo.get("pricingExpression", {}).get("usageUnit", "")

                        if price <= 0:
                            continue

                        per_1m = to_per_1m(price, unit)
                        if not per_1m:
                            # GCP often prices in USD per 1M directly for Gemini
                            per_1m = round(price, 6)

                        for r in matched_regions:
                            skus.append({
                                "provider":         provider_name,
                                "model":            normalize_model_name(desc),
                                "token_type":       token_type,
                                "price_per_1m_usd": per_1m,
                                "region":           r,
                                "raw_unit":         unit,
                                "source":           "GCP Cloud Billing API",
                                "fetched_at":       TODAY,
                            })
                        break
                    break

            token = data.get("nextPageToken")
            if not token:
                break
            pages += 1

    return {"provider": "GCP AI", "skus": skus}


# ─── OUTPUT ──────────────────────────────────────────────────────────────────

def write_csv(all_providers, path):
    """
    Write flat CSV with one row per provider × model × token_type × region.
    Also writes a 'summary' section with input+output combined per model.
    """
    rows = []
    for prov_data in all_providers:
        if prov_data.get("error"):
            print(f"  ✗ {prov_data.get('provider', '?'):25s}  {prov_data['error'][:55]}")
            continue

        provider_name = prov_data.get("provider", "")
        skus          = prov_data.get("skus", [])
        print(f"  ✓ {provider_name:25s}  {len(skus)} pricing rows")
        rows.extend(skus)

    if not rows:
        print("  No data to write.")
        return

    # Sort: provider → model → token_type → region
    rows.sort(key=lambda r: (
        r.get("provider", ""),
        r.get("model", ""),
        r.get("token_type", ""),
        r.get("region", ""),
    ))

    # Deduplicate: keep cheapest price for same (provider, model, token_type, region)
    deduped = {}
    for row in rows:
        key = (row["provider"], row["model"], row["token_type"], row["region"])
        if key not in deduped or row["price_per_1m_usd"] < deduped[key]["price_per_1m_usd"]:
            deduped[key] = row
    final_rows = sorted(deduped.values(),
                        key=lambda r: (r["provider"], r["model"],
                                       r["token_type"], r["region"]))

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(final_rows)

    print(f"\n  Wrote {path} ({os.path.getsize(path) // 1024} KB, {len(final_rows)} rows)")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 70)
    print("  INFERENCE API PRICING FETCH")
    print("=" * 70)
    print(f"  AWS Bedrock regions :  {', '.join(AWS_REGIONS)}")
    print(f"  Azure regions       :  {', '.join(AZURE_REGIONS)}")
    print(f"  GCP regions         :  {', '.join(GCP_REGIONS)}")
    print()

    print("→ Fetching AWS Bedrock ...")
    bedrock = fetch_bedrock()

    print("→ Fetching Azure OpenAI + AI Foundry ...")
    azure   = fetch_azure_inference()

    print("→ Fetching GCP Vertex AI / Gemini ...")
    vertex  = fetch_vertex()

    print("\n" + "=" * 70)
    print("  RESULTS")
    print("=" * 70)

    write_csv([bedrock, azure, vertex], INFERENCE_FILE)

    print()
    print("  TIP: Open inference_api_pricing.csv in Excel.")
    print("  Filter by 'provider' to see one cloud at a time.")
    print("  Filter by 'model' + token_type to compare input/output pricing.")
    print("  New models added by each provider are picked up automatically.")
    print()


if __name__ == "__main__":
    main()
