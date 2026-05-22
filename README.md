# GPU Pricing Intelligence

Live competitive pricing data — GPU cloud providers and LLM inference APIs — refreshed daily via GitHub Actions.

## What this does

Two daily scripts pull pricing from 8+ providers and commit the results as CSV files you can open directly in Excel or Google Sheets.

## Output files

| File | What's in it |
|---|---|
| `gpu_pricing_comparison.csv` | **Main file.** One row per GPU type. Neysa tiers vs Azure, AWS, GCP, OCI, RunPod, Vultr. India pricing preferred, US fallback. |
| `gpu_pricing_raw.csv` | Every GPU SKU from every provider. All tiers, all regions, all configs. For deep dives. |
| `gpu_pricing_history.csv` | Daily price snapshots going back to 2022. Open in Excel → PivotChart for trend lines. |
| `inference_api_pricing.csv` | Per-token pricing for LLM inference APIs: AWS Bedrock, Azure OpenAI, Azure AI Foundry, GCP Vertex AI / Gemini. |
| `gpu_pricing.json` | Raw JSON source data. Used by other scripts. |

## Scripts

| Script | Purpose |
|---|---|
| `fetch_gpu_pricing.py` | Pulls GPU on-demand + committed pricing from all cloud providers |
| `fetch_inference_pricing.py` | Pulls per-token inference pricing from Bedrock, Azure OpenAI, Vertex AI |
| `update_history.py` | Appends today's prices to the history file |

## Providers covered

**GPU pricing:** Azure · AWS EC2 · AWS SageMaker · GCP · Oracle Cloud · RunPod · Vultr · Vast.ai

**Inference API pricing:** AWS Bedrock · Azure OpenAI · Azure AI Foundry · GCP Vertex AI / Gemini

## Credentials (GitHub Secrets)

| Secret | Provider | Required? |
|---|---|---|
| `RUNPOD_API_KEY` | RunPod | Yes — free account, no card |
| `GCP_API_KEY` | GCP Billing API | Yes — free API key |
| `AWS_ACCESS_KEY_ID` | AWS Pricing API | Yes — read-only IAM user |
| `AWS_SECRET_ACCESS_KEY` | AWS Pricing API | Yes |

Azure and OCI work without any credentials (public APIs).

## Schedule

Runs daily at 6am UTC (11:30am IST). Trigger manually anytime via Actions → Run workflow.

## Adding new providers

Edit `fetch_gpu_pricing.py` — add a new `fetch_yourprovider()` function and append it to the `fetchers` list in `main()`. The comparison table picks it up automatically.
