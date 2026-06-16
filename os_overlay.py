#!/usr/bin/env python3
"""
os_overlay.py  —  OS tagging for fetch_gpu_pricing.py output
=============================================================
Two ways to use:

A) Minimal, no-fetcher-edit path (recommended to start):
   Run your existing fetch_gpu_pricing.py, then run:
       python os_overlay.py gpu_pricing.json
   It tags every SKU with an "os" field. Providers other than Azure are Linux.
   Azure SKUs are tagged Linux unless the name contains 'Windows'.

B) Fetcher edit (for full Windows visibility on Azure/AWS): apply the two
   diffs documented at the bottom of this file inside fetch_gpu_pricing.py.
"""
import json, sys

def tag_os(data):
    for pkey, pdata in data.get("providers", {}).items():
        for sku in pdata.get("skus", []):
            name = " ".join(str(sku.get(k, "")) for k in
                            ("sku", "instance_type", "display_name", "description"))
            sku["os"] = "Windows" if "windows" in name.lower() else "Linux"
    return data

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "gpu_pricing.json"
    with open(path) as f:
        data = json.load(f)
    data = tag_os(data)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    n = sum(len(p.get("skus", [])) for p in data["providers"].values())
    print(f"Tagged OS on {n} SKUs in {path}")

# ─────────────────────────────────────────────────────────────────────────────
# FETCHER DIFFS (apply inside fetch_gpu_pricing.py for native Windows pricing)
#
# AZURE  fetch_azure():  REPLACE the Windows-stripping filter
#   OLD:
#     items = [i for i in items
#              if "Windows" not in i.get("productName", "")
#              and "Spot" not in i.get("meterName", "")
#              and "Low Priority" not in i.get("meterName", "")]
#   NEW:
#     items = [i for i in items
#              if "Spot" not in i.get("meterName", "")
#              and "Low Priority" not in i.get("meterName", "")]
#   ...then when building each sku dict add:
#     "os": "Windows" if "Windows" in item.get("productName", "") else "Linux",
#   and include os in the seen-key tuple so Linux+Windows don't collide:
#     seen.add((arm_sku, region, "on_demand", os_name))
#
# All other providers: add  "os": "Linux"  to each appended sku dict.
# ─────────────────────────────────────────────────────────────────────────────
