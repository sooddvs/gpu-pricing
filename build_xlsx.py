#!/usr/bin/env python3
"""Build the comparison workbook (v2): USD+INR, specs, SKU name, India-first."""
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

HDR_FILL = PatternFill("solid", start_color="1F2A44")
HDR_FONT = Font(bold=True, color="FFFFFF", name="Arial", size=10)
NEYSA_FILL = PatternFill("solid", start_color="EAF4EA")
BLANK_FILL = PatternFill("solid", start_color="F2F2F2")
CHEAPER_FONT = Font(color="0A7D2C", name="Arial", size=10)
PRICIER_FONT = Font(color="C0392B", name="Arial", size=10)
BASE_FONT = Font(name="Arial", size=10)
THIN = Side(style="thin", color="C9C9C9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

# Descriptive headers, in column order.
COLUMNS = [
    ("neysa_sku",          "Neysa SKU"),
    ("neysa_gpu",          "Neysa GPU"),
    ("neysa_count",        "Neysa GPU Count"),
    ("neysa_os",           "Neysa OS"),
    ("neysa_vcpu",         "Neysa vCPU"),
    ("neysa_ram_gb",       "Neysa RAM (GB)"),
    ("tier",               "Commitment Tier"),
    ("neysa_usd_gpu_hr",   "Neysa Price (USD / GPU / hr)"),
    ("neysa_inr_gpu_hr",   "Neysa Price (INR / GPU / hr)"),
    ("neysa_tcv_usd",      "Neysa Total Contract Value (USD)"),
    ("neysa_tcv_inr",      "Neysa Total Contract Value (INR)"),
    ("provider",           "Competitor"),
    ("cmp_sku_name",       "Competitor SKU Name (verify online)"),
    ("cmp_gpu",            "Competitor GPU"),
    ("cmp_count",          "Competitor GPU Count"),
    ("cmp_vcpu",           "Competitor vCPU"),
    ("cmp_ram_gb",         "Competitor RAM (GB)"),
    ("cmp_os",             "Competitor OS"),
    ("cmp_region",         "Competitor Region"),
    ("cmp_region_bucket",  "Region Group"),
    ("cmp_usd_gpu_hr",     "Competitor Price (USD / GPU / hr)"),
    ("cmp_inr_gpu_hr",     "Competitor Price (INR / GPU / hr)"),
    ("cmp_tcv_usd",        "Competitor Total Contract Value (USD)"),
    ("cmp_tcv_inr",        "Competitor Total Contract Value (INR)"),
    ("match_status",       "Match Status"),
    ("neysa_vs_cmp_pct",   "Neysa vs Competitor (%)"),
    ("note",               "Notes"),
]

USD_COLS = {"neysa_usd_gpu_hr", "cmp_usd_gpu_hr"}
USD_TCV  = {"neysa_tcv_usd", "cmp_tcv_usd"}
INR_COLS = {"neysa_inr_gpu_hr", "cmp_inr_gpu_hr", "neysa_tcv_inr", "cmp_tcv_inr"}

TIER_ORDER = ["on_demand", "1mo", "6mo", "12mo", "24mo", "36mo"]


def build(rows, neysa_skus, out_path):
    wb = Workbook()
    _detail(wb.active, rows)
    _readme(wb.create_sheet("How to read"))
    wb.save(out_path)


def _detail(ws, rows):
    ws.title = "Comparison"
    keys = [k for k, _ in COLUMNS]
    heads = [h for _, h in COLUMNS]
    for j, h in enumerate(heads, 1):
        c = ws.cell(1, j, h); c.fill = HDR_FILL; c.font = HDR_FONT
        c.alignment = Alignment(horizontal="center", wrap_text=True); c.border = BORDER

    # stable sort: SKU, then tier order, then provider
    trank = {t: i for i, t in enumerate(TIER_ORDER)}
    rows = sorted(rows, key=lambda r: (r["neysa_sku"], trank.get(r["tier"], 9), r["provider"]))

    for i, r in enumerate(rows, 2):
        for j, key in enumerate(keys, 1):
            val = r.get(key)
            c = ws.cell(i, j, val); c.font = BASE_FONT; c.border = BORDER
            c.alignment = Alignment(horizontal="left" if key in ("note", "cmp_sku_name", "neysa_sku") else "center",
                                    wrap_text=(key == "note"))
            if key in USD_COLS:
                c.number_format = '$#,##0.0000'
            elif key in USD_TCV:
                c.number_format = '$#,##0'
            elif key in INR_COLS:
                c.number_format = '\u20b9#,##0'
            elif key == "neysa_vs_cmp_pct" and isinstance(val, (int, float)):
                c.value = val / 100; c.number_format = '+0.0%;-0.0%'
                c.font = CHEAPER_FONT if val < 0 else PRICIER_FONT
            # grey out blank competitor price cells
            if key in (USD_COLS | INR_COLS | USD_TCV) and val is None and r.get("provider"):
                if key.startswith("cmp"):
                    c.fill = BLANK_FILL
            if key.startswith("neysa_") and key in (USD_COLS | INR_COLS):
                c.fill = NEYSA_FILL

    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(keys))}{len(rows)+1}"
    widths = {
        "Neysa SKU": 16, "Competitor SKU Name (verify online)": 30, "Notes": 60,
        "Commitment Tier": 14, "Competitor": 14, "Competitor Region": 16,
    }
    for j, (_, h) in enumerate(COLUMNS, 1):
        ws.column_dimensions[get_column_letter(j)].width = widths.get(h, 13)


def _readme(ws):
    ws.sheet_view.showGridLines = False
    L = [
        ("How to read this comparison", True, 13),
        ("", False, 10),
        ("One row per Neysa SKU x commitment tier x competitor.", False, 10),
        ("All prices shown in BOTH USD and INR (converted at 94.61 INR/USD).", False, 10),
        ("", False, 10),
        ("Key columns", True, 11),
        ("Neysa vs Competitor (%): negative = Neysa is cheaper (good); positive = pricier.", False, 10),
        ("Match Status:", True, 10),
        ("   exact            = competitor sells the same GPU at this tier; prices shown.", False, 10),
        ("   tier not offered = competitor has the GPU but not this commitment length;", False, 10),
        ("                      price left blank, see Notes.", False, 10),
        ("   no GPU match     = competitor does not sell this GPU at all; price left", False, 10),
        ("                      blank, and Notes describes their nearest SKU + cost.", False, 10),
        ("Competitor SKU Name: paste into the provider's pricing page to verify.", False, 10),
        ("", False, 10),
        ("Regions", True, 11),
        ("India pricing is preferred. If a competitor has no India SKU, the nearest", False, 10),
        ("region is used in this order: India > Singapore > other APAC > US > global.", False, 10),
        ("The region group used is shown, and called out in Notes when it is not India.", False, 10),
        ("", False, 10),
        ("Specs", True, 11),
        ("vCPU and RAM are shown for both sides where the provider exposes them.", False, 10),
        ("When a competitor SKU has fewer vCPU or less RAM than the Neysa SKU, this is", False, 10),
        ("flagged in Notes, so a lower competitor price is not mistaken for like-for-like.", False, 10),
        ("Some providers (notably GCP) may not expose vCPU/RAM; those cells stay blank.", False, 10),
        ("", False, 10),
        ("Assumptions", True, 11),
        ("FX 94.61 INR/USD; 730-hour month; Ubuntu/Linux pricing only; A100 excluded.", False, 10),
        ("No estimated/synthesised prices: a blank price means the competitor does not", False, 10),
        ("publish that exact tier or GPU. Nothing is invented.", False, 10),
    ]
    for i, (t, b, s) in enumerate(L, 1):
        ws.cell(i, 1, t).font = Font(bold=b, size=s, name="Arial",
                                     color="1F2A44" if b else "000000")
    ws.column_dimensions["A"].width = 95
