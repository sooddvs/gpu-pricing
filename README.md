# GPU Pricing Tracker

Fetches live GPU pricing daily from Azure, OCI, AWS, GCP, DigitalOcean, and RunPod.

## Outputs
- `gpu_pricing.json` — full structured data
- `gpu_pricing.csv` — flat summary, open in Excel/Sheets

## Secrets needed
| Secret | Where to get it |
|--------|----------------|
| `RUNPOD_API_KEY` | runpod.io/console/user/settings |
| `GCP_API_KEY` | console.cloud.google.com/apis/credentials |
| `DO_API_TOKEN` | cloud.digitalocean.com/account/api/tokens |
| `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` | AWS IAM console |

Azure and OCI work with no credentials at all.
