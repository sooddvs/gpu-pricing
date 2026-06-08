# GPU Pricing Intelligence

Automated daily pipeline that pulls live GPU rental and LLM inference pricing from 10+ cloud providers and commits the results as analysis-ready CSV files.

Runs every morning at **6am UTC (11:30am IST)** via GitHub Actions. No manual work needed after setup.

---

## What it tracks

### GPU rental pricing
Hourly and committed rates per GPU across:

| Provider | Type |
|---|---|
| Azure | Hyperscaler |
| AWS EC2 | Hyperscaler |
| AWS SageMaker | Managed ML platform |
| Google Cloud | Hyperscaler |
| Oracle Cloud | Hyperscaler |
| RunPod | GPU cloud |
| Vultr | GPU cloud |
| Vast.ai | Marketplace |

For each provider: on-demand, 1-month, 6-month, 12-month, and 36-month rates where available. India pricing is preferred; falls back to US if India region has no data.

### LLM inference API pricing
Per-token rates ($/1M tokens, input and output) across:

| Provider | Models covered |
|---|---|
| AWS Bedrock | Llama, Mistral, Claude, DeepSeek, Amazon Nova, Titan, and others |
| Azure OpenAI | GPT-4o, o1, o3, GPT-4.1, GPT-5, and others |
| Azure AI Foundry | Llama, Mistral, DeepSeek, Phi, and others |
| GCP Vertex AI / Gemini | Gemini 2.x, 2.5, 3.x, and others |

New models added by any provider are picked up automatically on the next run — no script changes needed.

---

## Output files

| File | Updated | What's in it |
|---|---|---|
| `gpu_pricing_comparison.csv` | Daily | **Main file.** One row per GPU type. Pricing tiers across all providers side by side. |
| `gpu_pricing_raw.csv` | Daily | Every GPU SKU from every provider — all tiers, all regions, all configs. |
| `gpu_pricing_history.csv` | Daily | Cumulative daily snapshots going back to 2022. Use for trend charts. |
| `inference_api_pricing.csv` | Daily | Per-token LLM inference pricing across Bedrock, Azure, and Vertex AI. |
| `price_changes.csv` | On change | Logs any price move ≥ 5% with old price, new price, and % change. Only has rows on days something moved. |
| `gpu_pricing.json` | Daily | Raw structured data. Source for all the CSVs above. |

---

## How to use each file

**`gpu_pricing_comparison.csv`** — open in Excel or Google Sheets. Filter by `gpu_type` (H100 SXM, H200 SXM, L40S, etc.) to see on-demand and committed tiers across every provider side by side. The first two rows are metadata and a column guide — data starts on row 3.

**`gpu_pricing_history.csv`** — open in Excel → select all → Insert → PivotChart → Line chart. Set axis to `date`, series to `provider`, values to `per_gpu_hourly_usd`. Filter `gpu_type` to one GPU at a time. Historical estimates (pre-2026) are labelled `source=historical_estimate`; live data is `source=live`.

**`inference_api_pricing.csv`** — filter by `provider` to compare one cloud at a time. `token_type` is either `input` or `output`. All prices are normalised to USD per 1 million tokens regardless of how each provider originally publishes them.

**`price_changes.csv`** — check this whenever you need to know if any provider has moved prices recently. `direction=DOWN` means a price cut. Sorted by absolute change descending, so the biggest moves are at the top.

---

## Scripts

| Script | What it does |
|---|---|
| `fetch_gpu_pricing.py` | Pulls GPU pricing from all 8 providers, writes JSON + CSVs |
| `fetch_inference_pricing.py` | Pulls per-token inference pricing from Bedrock, Azure, Vertex AI |
| `update_history.py` | Appends today's prices to history file, runs price change detection |

---

## Setup

**Credentials (stored as GitHub repository secrets):**

| Secret | Where to get it | Card needed? |
|---|---|---|
| `RUNPOD_API_KEY` | runpod.io → Settings → API Keys | No |
| `GCP_API_KEY` | console.cloud.google.com/apis/credentials | Yes (free tier) |
| `AWS_ACCESS_KEY_ID` | AWS IAM → create user with `AWSPriceListServiceFullAccess` | Yes |
| `AWS_SECRET_ACCESS_KEY` | Same as above | Yes |

Azure and Oracle Cloud work with no credentials — their pricing APIs are fully public.

**To run manually:** Actions tab → Fetch GPU pricing daily → Run workflow.

---

## Extending it

To add a new GPU provider, add a `fetch_newprovider()` function to `fetch_gpu_pricing.py` and append it to the `fetchers` list in `main()`. The comparison table picks it up automatically.

To change the price change alert threshold (default 5%), edit `CHANGE_THRESHOLD` at the top of `update_history.py`.

To add a new GPU type to the baseline pricing, update the `NEYSA` dict at the top of `fetch_gpu_pricing.py`.
