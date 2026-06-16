#!/usr/bin/env python3
"""
Neysa Competitor SKU Matcher  (v2)
==================================
For each Neysa GPU SKU, find the closest competitor SKU per provider and lay
the prices side by side in both USD and INR.

v2 changes
----------
- Region priority is India-first, then near-India: India > Singapore >
  other APAC > US > global. The region actually used is named in the notes.
- Both USD and INR are shown for every price (FX = USD_INR).
- NO tier synthesis. If a competitor does not publish one of Neysa's commitment
  tiers, the price cell is left blank and the note says they do not offer it.
- If a competitor does not sell Neysa's GPU at all, the price cells are left
  blank and the note describes their NEAREST SKU (GPU, config, cost) as context.
- Competitor SKU name is surfaced so sales can verify on the provider's page.
- Competitor specs (vCPU / RAM / disk) are shown where available and compared
  to Neysa's; a flag is raised when the competitor offers fewer vCPU / less RAM.
- Ubuntu / Linux pricing only (OS_FILTER).
"""

import argparse, json, math, re

USD_INR = 94.61
HOURS_PER_MONTH = 730
OS_FILTER = "Linux"

GPU_LADDER = ["L4", "L40S", "H100 NVL", "H100 SXM", "H200 SXM", "B200", "B300"]
NEYSA_TERM_MONTHS = {"1mo": 1, "6mo": 6, "12mo": 12, "24mo": 24, "36mo": 36}

INDIA_REGIONS     = {"ap-south-1", "asia-south1", "southindia", "centralindia",
                     "westindia", "ap-south-2"}
SINGAPORE_REGIONS = {"ap-southeast-1", "asia-southeast1", "southeastasia"}
APAC_REGIONS      = {"ap-southeast-2", "ap-southeast-3", "ap-northeast-1",
                     "ap-northeast-2", "ap-northeast-3", "ap-east-1",
                     "asia-northeast1", "asia-northeast3", "asia-east1",
                     "asia-east2", "asia-southeast2", "japaneast", "japanwest",
                     "koreacentral", "eastasia", "southeastasia2"}
US_REGIONS        = {"us-east-1", "us-east-2", "us-west-1", "us-west-2",
                     "us-central1", "eastus", "eastus2", "westus2", "westus"}

REGION_LABEL = {0: "India", 1: "Singapore", 2: "APAC", 3: "US", 4: "global/other"}


def region_rank(region):
    if region in INDIA_REGIONS: return 0
    if region in SINGAPORE_REGIONS: return 1
    if region in APAC_REGIONS: return 2
    if region in US_REGIONS: return 3
    return 4


def region_bucket_name(region):
    return REGION_LABEL[region_rank(region)]


def ladder_distance(a, b):
    try:
        return abs(GPU_LADDER.index(a) - GPU_LADDER.index(b))
    except ValueError:
        return math.inf


def usd(x): return None if x is None else round(x, 4)
def inr(x): return None if x is None else round(x * USD_INR, 0)


def parse_config(cfg):
    vcpu = ram = None
    if cfg:
        m = re.search(r"(\d+)\s*vCPU", cfg, re.I)
        if m: vcpu = int(m.group(1))
        m = re.search(r"(\d+)\s*GB\s*RAM", cfg, re.I)
        if m: ram = int(m.group(1))
    return vcpu, ram


def load_neysa(path):
    with open(path) as f:
        raw = json.load(f)
    skus = []
    for s in raw:
        gpu, count = s["gpu_type"], s["gpu_count"]
        tiers = {}
        od = s.get("on_demand_inr_hr")
        if od:
            tiers["on_demand"] = (od / USD_INR) / count
        for tier, monthly_inr in (s.get("monthly_inr") or {}).items():
            if monthly_inr:
                tiers[tier] = (monthly_inr / HOURS_PER_MONTH / USD_INR) / count
        vcpu, ram = parse_config(s.get("config") or s.get("sku_config") or "")
        skus.append({
            "gpu_type": gpu, "gpu_count": count,
            "label": s.get("label", f"{count} x {gpu}"),
            "os": s.get("os", "Ubuntu"),
            "vcpu": vcpu, "ram_gb": ram, "vmem": s.get("vmem"),
            "usd_per_gpu_hr": tiers,
        })
    return skus


def load_competitors(path):
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
            rows.append({
                "gpu_type": sku.get("gpu_type"),
                "gpu_count": sku.get("gpu_count", 1),
                "region": sku.get("region", ""),
                "tier": sku.get("price_tier", "on_demand"),
                "per_gpu_hr": float(pg),
                "os": sku.get("os") or "Linux",
                "vcpu": sku.get("vcpu"),
                "ram_gb": sku.get("ram_gb"),
                "disk": sku.get("disk"),
                "name": (sku.get("sku") or sku.get("instance_type")
                         or sku.get("display_name") or sku.get("part_number")
                         or sku.get("id") or sku.get("description") or ""),
            })
        providers[name] = rows
    return providers


def _os_ok(r):
    return OS_FILTER is None or r.get("os", "Linux") == OS_FILTER


def best_sku_for(rows, gpu_type, target_count, tier):
    cands = [r for r in rows
             if r["gpu_type"] == gpu_type and r["tier"] == tier and _os_ok(r)]
    if not cands:
        return None
    cands.sort(key=lambda r: (region_rank(r["region"]),
                              abs((r["gpu_count"] or 1) - target_count),
                              r["per_gpu_hr"]))
    return cands[0]


def nearest_sku_any_tier(rows, gpu_type, target_count):
    have = {r["gpu_type"] for r in rows if _os_ok(r)}
    ranked = sorted([g for g in have if ladder_distance(gpu_type, g) != math.inf],
                    key=lambda g: ladder_distance(gpu_type, g))
    if not ranked:
        return None
    near_gpu = ranked[0]
    cands = [r for r in rows if r["gpu_type"] == near_gpu and _os_ok(r)]
    cands.sort(key=lambda r: (0 if r["tier"] == "on_demand" else 1,
                              region_rank(r["region"]),
                              abs((r["gpu_count"] or 1) - target_count),
                              r["per_gpu_hr"]))
    return cands[0] if cands else None


def spec_note(neysa_sku, cmp_row):
    flags = []
    nv, nr = neysa_sku.get("vcpu"), neysa_sku.get("ram_gb")
    cv, cr = cmp_row.get("vcpu"), cmp_row.get("ram_gb")
    if nv and cv and cv < nv:
        flags.append(f"fewer vCPU ({cv} vs {nv})")
    if nr and cr and cr < nr:
        flags.append(f"less RAM ({cr}GB vs {nr}GB)")
    return "; ".join(flags)


def match_all(neysa_skus, competitors):
    results = []
    all_tiers = ["on_demand"] + list(NEYSA_TERM_MONTHS.keys())
    for nsku in neysa_skus:
        gpu, count = nsku["gpu_type"], nsku["gpu_count"]
        for prov_name, rows in competitors.items():
            prov_gpus = {r["gpu_type"] for r in rows if _os_ok(r)}
            has_gpu = gpu in prov_gpus
            no_gpu_note = ""
            if not has_gpu:
                near = nearest_sku_any_tier(rows, gpu, count)
                if near:
                    cfg_bits = []
                    if near.get("vcpu"): cfg_bits.append(f"{near['vcpu']} vCPU")
                    if near.get("ram_gb"): cfg_bits.append(f"{near['ram_gb']}GB RAM")
                    cfg = ", ".join(cfg_bits)
                    reg = region_bucket_name(near["region"])
                    no_gpu_note = (f"{prov_name} does not offer {gpu}. "
                                   f"Nearest: {near['name'] or near['gpu_type']} "
                                   f"({near['gpu_type']}" + (f", {cfg}" if cfg else "") + ") "
                                   f"at ${near['per_gpu_hr']:.2f}/GPU/hr "
                                   f"(\u20b9{near['per_gpu_hr']*USD_INR:,.0f}) [{reg} region].")
                else:
                    no_gpu_note = f"{prov_name} does not offer {gpu} or any comparable GPU."
            for tier in all_tiers:
                neysa_price = nsku["usd_per_gpu_hr"].get(tier)
                term_months = NEYSA_TERM_MONTHS.get(tier)
                neysa_tcv = (neysa_price * HOURS_PER_MONTH * term_months
                             if (term_months and neysa_price) else None)
                base = {
                    "neysa_sku": nsku["label"], "neysa_gpu": gpu, "neysa_count": count,
                    "neysa_os": nsku.get("os", "Ubuntu"),
                    "neysa_vcpu": nsku.get("vcpu"), "neysa_ram_gb": nsku.get("ram_gb"),
                    "tier": tier,
                    "neysa_usd_gpu_hr": usd(neysa_price), "neysa_inr_gpu_hr": inr(neysa_price),
                    "neysa_tcv_usd": round(neysa_tcv, 0) if neysa_tcv else None,
                    "neysa_tcv_inr": inr(neysa_tcv) if neysa_tcv else None,
                    "provider": prov_name,
                }
                if not has_gpu:
                    results.append({**base, "cmp_sku_name": "", "cmp_gpu": "",
                        "cmp_count": None, "cmp_os": "", "cmp_region": "",
                        "cmp_region_bucket": "", "cmp_usd_gpu_hr": None,
                        "cmp_inr_gpu_hr": None, "cmp_tcv_usd": None, "cmp_tcv_inr": None,
                        "cmp_vcpu": None, "cmp_ram_gb": None,
                        "match_status": "no GPU match", "neysa_vs_cmp_pct": None,
                        "note": no_gpu_note})
                    continue
                direct = best_sku_for(rows, gpu, count, tier)
                if not direct:
                    results.append({**base, "cmp_sku_name": "", "cmp_gpu": gpu,
                        "cmp_count": None, "cmp_os": "", "cmp_region": "",
                        "cmp_region_bucket": "", "cmp_usd_gpu_hr": None,
                        "cmp_inr_gpu_hr": None, "cmp_tcv_usd": None, "cmp_tcv_inr": None,
                        "cmp_vcpu": None, "cmp_ram_gb": None,
                        "match_status": "tier not offered", "neysa_vs_cmp_pct": None,
                        "note": f"{prov_name} does not offer a {tier} commitment for {gpu}."})
                    continue
                per_gpu = direct["per_gpu_hr"]
                tcv = per_gpu * HOURS_PER_MONTH * term_months if term_months else None
                delta = ((neysa_price - per_gpu) / per_gpu * 100
                         if (neysa_price and per_gpu) else None)
                reg_bucket = region_bucket_name(direct["region"])
                notes = []
                if reg_bucket != "India":
                    notes.append(f"No India SKU; used {reg_bucket} region ({direct['region']}).")
                sn = spec_note(nsku, direct)
                if sn:
                    notes.append("Competitor " + sn + ".")
                results.append({**base,
                    "cmp_sku_name": direct["name"], "cmp_gpu": direct["gpu_type"],
                    "cmp_count": direct["gpu_count"], "cmp_os": direct.get("os", "Linux"),
                    "cmp_region": direct["region"], "cmp_region_bucket": reg_bucket,
                    "cmp_usd_gpu_hr": usd(per_gpu), "cmp_inr_gpu_hr": inr(per_gpu),
                    "cmp_tcv_usd": round(tcv, 0) if tcv else None,
                    "cmp_tcv_inr": inr(tcv) if tcv else None,
                    "cmp_vcpu": direct.get("vcpu"), "cmp_ram_gb": direct.get("ram_gb"),
                    "match_status": "exact",
                    "neysa_vs_cmp_pct": round(delta, 1) if delta is not None else None,
                    "note": " ".join(notes)})
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
        print("WARNING: no rows produced."); return
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
        print(f"xlsx build skipped/failed ({e}); CSV still written.")


if __name__ == "__main__":
    main()
