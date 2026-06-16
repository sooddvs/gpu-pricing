#!/usr/bin/env python3
"""
enrich_specs.py  —  add vCPU / RAM / disk to gpu_pricing.json
=============================================================
Runs AFTER fetch_gpu_pricing.py. Adds spec fields to each competitor SKU so the
matcher can show and compare vCPU/RAM. Two sources of specs:

1. STATIC TABLE (below): instance specs that are public and stable. Covers AWS
   EC2/SageMaker, GCP A2/A3/G2 machine types, Azure ND/NC/NV, Vultr, OCI shapes.
   This is the reliable path and needs no extra API calls or credentials.

2. (Optional) Live enrichment: AWS exposes vcpu/memory on the Pricing API
   record; if you edit the fetcher you can capture them directly. The static
   table is used as the default and as a fallback when the live field is absent.

Usage:
    python enrich_specs.py gpu_pricing.json

GCP NOTE: GCP prices GPUs and the host VM (vCPU/RAM) as separate line items, so
the fetched GPU SKUs have no bundled vCPU/RAM. We attach the vCPU/RAM of the
standard machine type usually paired with each GPU slice (documented below).
These are typical pairings, not a guarantee of what a customer configures.
"""
import sys, json

# key by instance/sku/display identifier substring -> (vcpu, ram_gb, disk)
# Specs are per the documented instance definition (whole node).
AWS_EC2 = {
    "p5.48xlarge":      (192, 2048, "8x3.84TB NVMe"),
    "p5e.48xlarge":     (192, 2048, "8x3.84TB NVMe"),
    "p5en.48xlarge":    (192, 2048, "8x3.84TB NVMe"),
    "p6-b200.48xlarge": (192, 2048, "8x3.84TB NVMe"),
    "p4d.24xlarge":     (96,  1152, "8x1TB NVMe"),
    "p4de.24xlarge":    (96,  1152, "8x1TB NVMe"),
    "g6.xlarge":        (4,   16,   "250GB NVMe"),
    "g6.4xlarge":       (16,  64,   "600GB NVMe"),
    "g6.12xlarge":      (48,  192,  "3.76TB NVMe"),
    "g6.48xlarge":      (192, 768,  "7.6TB NVMe"),
    "g6e.xlarge":       (4,   32,   "250GB NVMe"),
    "g6e.4xlarge":      (16,  128,  "600GB NVMe"),
    "g6e.12xlarge":     (48,  384,  "3.8TB NVMe"),
    "g6e.48xlarge":     (192, 1536, "7.6TB NVMe"),
}
# SageMaker uses ml.<same>; reuse AWS_EC2 by stripping "ml."
GCP_MACHINE = {  # GPU type -> typical paired machine type vCPU/RAM per GPU slice
    # A3 High (H100 80GB) a3-highgpu-8g = 208 vCPU / 1872GB for 8 GPUs
    "H100 SXM": (26, 234, "host SSD"),     # per-GPU slice of a3-highgpu-8g
    "H200 SXM": (28, 234, "host SSD"),     # a3-ultragpu per-GPU slice approx
    "A100":     (12, 85,  "host SSD"),     # a2-highgpu-1g
    "L4":       (8,  32,  "host SSD"),     # g2-standard-8
    "B200":     (28, 234, "host SSD"),     # a4 per-GPU slice approx
}
AZURE = {
    "Standard_ND96isr_H100_v5":  (96, 1900, "8x1TB NVMe"),
    "Standard_ND96isr_H200_v5":  (96, 1900, "8x1TB NVMe"),
    "Standard_NC40ads_H100_v5":  (40, 320,  "1x500GB"),
    "Standard_NC80adis_H100_v5": (80, 640,  "2x500GB"),
    "Standard_NC24ads_A100_v4":  (24, 220,  "1x1TB"),
    "Standard_NC48ads_A100_v4":  (48, 440,  "2x1TB"),
    "Standard_NC96ads_A100_v4":  (96, 880,  "4x1TB"),
    "Standard_ND96amsr_A100_v4": (96, 1900, "8x1TB"),
}
VULTR = {  # plan_id substring -> specs (already partly in plan id)
    "vcg-l40s-16c-180g":   (16,  180,  "48GB VRAM"),
    "vcg-l40s-32c-375g":   (32,  375,  "96GB VRAM"),
    "vcg-l40s-64c-750g":   (64,  750,  "192GB VRAM"),
    "vcg-l40s-128c-1500g": (128, 1500, "384GB VRAM"),
}
OCI = {  # display name substring -> per-GPU approximate
    "H100": (None, None, None),  # OCI BM.GPU shapes vary; left blank to be honest
    "H200": (None, None, None),
    "L40S": (None, None, None),
    "A100": (None, None, None),
    "B200": (None, None, None),
    "B300": (None, None, None),
}


def lookup(provider, sku):
    name = (sku.get("instance_type") or sku.get("sku") or sku.get("plan_id")
            or sku.get("display_name") or sku.get("id") or "")
    gpu = sku.get("gpu_type", "")
    if provider in ("AWS EC2", "AWS SageMaker"):
        key = name.replace("ml.", "")
        return AWS_EC2.get(key)
    if provider == "Azure":
        return AZURE.get(name)
    if provider == "Vultr":
        for k, v in VULTR.items():
            if k in name:
                return v
    if provider == "GCP":
        return GCP_MACHINE.get(gpu)
    if provider == "OCI":
        return OCI.get(gpu.split()[0] if gpu else "")
    return None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "gpu_pricing.json"
    with open(path) as f:
        data = json.load(f)
    n = filled = 0
    for pdata in data.get("providers", {}).values():
        prov = pdata.get("provider", "")
        for sku in pdata.get("skus", []):
            n += 1
            # don't overwrite live specs if the fetcher already captured them
            if sku.get("vcpu") and sku.get("ram_gb"):
                filled += 1
                continue
            spec = lookup(prov, sku)
            if spec:
                vcpu, ram, disk = spec
                if vcpu is not None:
                    # specs in the table are whole-node; matcher works per-GPU
                    # but vCPU/RAM are shown as node totals for context.
                    sku["vcpu"] = vcpu
                    sku["ram_gb"] = ram
                    sku["disk"] = disk
                    filled += 1
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"enrich_specs: {filled}/{n} SKUs now have vCPU/RAM specs in {path}")


if __name__ == "__main__":
    main()
