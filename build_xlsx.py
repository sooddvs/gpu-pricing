#!/usr/bin/env python3
"""Build the comparison workbook from matcher rows."""
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

HDR_FILL = PatternFill("solid", start_color="1F2A44")
HDR_FONT = Font(bold=True, color="FFFFFF", name="Arial", size=10)
SUB_FILL = PatternFill("solid", start_color="E8EBF2")
SYNTH_FILL = PatternFill("solid", start_color="FFF3CD")   # amber = estimated
NEYSA_FILL = PatternFill("solid", start_color="EAF4EA")
CHEAPER_FONT = Font(color="0A7D2C", name="Arial", size=10)   # Neysa cheaper (good)
PRICIER_FONT = Font(color="C0392B", name="Arial", size=10)   # Neysa pricier
BASE_FONT = Font(name="Arial", size=10)
THIN = Side(style="thin", color="C9C9C9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

TIER_ORDER = ["on_demand", "1mo", "6mo", "12mo", "24mo", "36mo"]
TIER_LABEL = {"on_demand": "On-Demand", "1mo": "1-mo", "6mo": "6-mo",
              "12mo": "12-mo", "24mo": "24-mo", "36mo": "36-mo"}


def build(rows, neysa_skus, out_path):
    wb = Workbook()
    _matrix_sheet(wb.active, rows)
    _detail_sheet(wb.create_sheet("Detail (all rows)"), rows)
    _readme_sheet(wb.create_sheet("How to read"))
    wb.save(out_path)


def _matrix_sheet(ws, rows):
    """One block per Neysa SKU: tiers as rows, providers as columns,
       showing competitor USD/GPU/hr and Neysa's delta%."""
    ws.title = "Comparison"
    ws.sheet_view.showGridLines = False

    providers = sorted({r["provider"] for r in rows})
    # group rows: (neysa_sku) -> (provider, tier, gpu_match) -> row
    skus = []
    seen = set()
    for r in rows:
        if r["neysa_sku"] not in seen:
            seen.add(r["neysa_sku"]); skus.append(r["neysa_sku"])

    col = 1
    ws.cell(1, 1, "Neysa SKU vs nearest competitor SKUs  —  USD per GPU per hour (India-preferred regions)")
    ws.cell(1, 1).font = Font(bold=True, size=12, name="Arial")
    ws.cell(2, 1, "Amber = estimated/synthesised tier (competitor lacked it). Green = Neysa cheaper. Red = Neysa pricier. % = Neysa vs competitor.")
    ws.cell(2, 1).font = Font(italic=True, size=9, name="Arial", color="555555")
    ws.cell(3, 1, "OS basis: Ubuntu / Linux on-demand pricing only (Neysa VMs are Ubuntu). Windows and other-OS competitor pricing is excluded here; see Detail tab to change.")
    ws.cell(3, 1).font = Font(italic=True, size=9, name="Arial", color="555555")

    row_ptr = 5
    for sku in skus:
        sku_rows = [r for r in rows if r["neysa_sku"] == sku]
        # header for this SKU block
        c = ws.cell(row_ptr, 1, sku)
        c.font = Font(bold=True, size=11, name="Arial", color="1F2A44")
        c.fill = SUB_FILL
        ws.merge_cells(start_row=row_ptr, start_column=1, end_row=row_ptr, end_column=2 + len(providers))
        row_ptr += 1

        # column headers
        ws.cell(row_ptr, 1, "Tier"); ws.cell(row_ptr, 2, "Neysa $/GPU/hr")
        for j, p in enumerate(providers):
            ws.cell(row_ptr, 3 + j, p)
        for cc in range(1, 3 + len(providers)):
            hc = ws.cell(row_ptr, cc); hc.fill = HDR_FILL; hc.font = HDR_FONT
            hc.alignment = Alignment(horizontal="center", wrap_text=True)
            hc.border = BORDER
        row_ptr += 1

        # which gpu types are being compared (note line per provider)
        gpu_note = {}
        for p in providers:
            kinds = {r["cmp_gpu"]: r["gpu_match"] for r in sku_rows if r["provider"] == p}
            if kinds:
                gpu_note[p] = ", ".join(f"{g}" if k.startswith("exact")
                                        else f"{g}*" for g, k in kinds.items())

        for tier in TIER_ORDER:
            ws.cell(row_ptr, 1, TIER_LABEL[tier]).font = BASE_FONT
            ws.cell(row_ptr, 1).border = BORDER
            # neysa price
            np_val = next((r["neysa_usd_gpu_hr"] for r in sku_rows if r["tier"] == tier), None)
            nc = ws.cell(row_ptr, 2, np_val if np_val is not None else "—")
            nc.fill = NEYSA_FILL; nc.font = Font(bold=True, name="Arial", size=10)
            nc.alignment = Alignment(horizontal="center"); nc.border = BORDER
            if isinstance(np_val, (int, float)):
                nc.number_format = "$#,##0.00"

            for j, p in enumerate(providers):
                cell = ws.cell(row_ptr, 3 + j)
                cell.border = BORDER
                cell.alignment = Alignment(horizontal="center")
                # pick the best (exact-gpu preferred) row for this provider+tier
                cand = [r for r in sku_rows if r["provider"] == p and r["tier"] == tier]
                if not cand:
                    cell.value = "—"; cell.font = BASE_FONT; continue
                cand.sort(key=lambda r: (0 if r["gpu_match"].startswith("exact") else 1,
                                          0 if r["tier_status"] == "exact" else 1))
                best = cand[0]
                price = best["cmp_usd_gpu_hr"]
                delta = best["neysa_vs_cmp_pct"]
                txt = f"${price:,.2f}"
                if delta is not None:
                    txt += f"\n({delta:+.0f}%)"
                cell.value = txt
                if best["tier_status"] == "synth":
                    cell.fill = SYNTH_FILL
                if delta is not None:
                    cell.font = CHEAPER_FONT if delta < 0 else PRICIER_FONT
                else:
                    cell.font = BASE_FONT
                cell.alignment = Alignment(horizontal="center", wrap_text=True)
            row_ptr += 1

        # gpu-match note row
        ws.cell(row_ptr, 1, "GPU matched:").font = Font(italic=True, size=8, name="Arial", color="777777")
        for j, p in enumerate(providers):
            n = gpu_note.get(p, "—")
            gc = ws.cell(row_ptr, 3 + j, n)
            gc.font = Font(italic=True, size=8, name="Arial", color="777777")
            gc.alignment = Alignment(horizontal="center", wrap_text=True)
        ws.cell(row_ptr, 2, "(* = nearest, not exact)").font = Font(italic=True, size=8, name="Arial", color="777777")
        row_ptr += 2

    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 15
    for j in range(len(providers)):
        ws.column_dimensions[get_column_letter(3 + j)].width = 14
    ws.freeze_panes = "C5"


def _detail_sheet(ws, rows):
    cols = ["neysa_sku", "neysa_gpu", "neysa_count", "neysa_os", "tier",
            "neysa_usd_gpu_hr", "neysa_tcv_usd", "provider", "gpu_match",
            "cmp_gpu", "cmp_count", "cmp_os", "cmp_region", "cmp_usd_gpu_hr",
            "cmp_tcv_usd", "tier_status", "neysa_vs_cmp_pct", "note"]
    headers = ["Neysa SKU", "Neysa GPU", "Count", "Neysa OS", "Tier", "Neysa $/GPU/hr",
               "Neysa TCV $", "Provider", "GPU match", "Cmp GPU", "Cmp count",
               "Cmp OS", "Cmp region", "Cmp $/GPU/hr", "Cmp TCV $", "Tier status",
               "Neysa vs Cmp %", "Synth note"]
    for j, h in enumerate(headers, 1):
        c = ws.cell(1, j, h); c.fill = HDR_FILL; c.font = HDR_FONT
        c.alignment = Alignment(horizontal="center", wrap_text=True); c.border = BORDER
    for i, r in enumerate(rows, 2):
        for j, key in enumerate(cols, 1):
            c = ws.cell(i, j, r.get(key))
            c.font = BASE_FONT; c.border = BORDER
            if key in ("neysa_usd_gpu_hr", "cmp_usd_gpu_hr"):
                c.number_format = "$#,##0.0000"
            elif key in ("neysa_tcv_usd", "cmp_tcv_usd"):
                c.number_format = "$#,##0"
            elif key == "neysa_vs_cmp_pct":
                c.number_format = "+0.0%;-0.0%"
                if isinstance(r.get(key), (int, float)):
                    c.value = r[key] / 100
            if r.get("tier_status") == "synth":
                c.fill = SYNTH_FILL
    ws.freeze_panes = "A2"
    widths = [22, 11, 7, 9, 9, 14, 13, 14, 16, 11, 10, 8, 12, 14, 13, 11, 14, 40]
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{len(rows)+1}"


def _readme_sheet(ws):
    ws.sheet_view.showGridLines = False
    lines = [
        ("How to read this workbook", True, 13),
        ("", False, 10),
        ("Comparison tab", True, 11),
        ("One block per Neysa SKU. Rows are commitment tiers; columns are competitors.", False, 10),
        ("Each cell shows the competitor's closest SKU price in USD per GPU per hour,", False, 10),
        ("with Neysa's price difference in brackets. Negative % = Neysa is cheaper.", False, 10),
        ("", False, 10),
        ("Colour key", True, 11),
        ("Green number  = Neysa is cheaper than that competitor at that tier.", False, 10),
        ("Red number    = Neysa is more expensive.", False, 10),
        ("Amber cell    = competitor did NOT publish that tier; value is estimated", False, 10),
        ("                by scaling their nearest tier using Neysa's own tier ratios.", False, 10),
        ("                See the Detail tab 'Synth note' for which SKU was used.", False, 10),
        ("GPU matched row = which competitor GPU was compared. '*' means it is the", False, 10),
        ("                nearest GPU on the capability ladder, not an exact match", False, 10),
        ("                (used only when the competitor has no same-GPU SKU).", False, 10),
        ("", False, 10),
        ("Detail tab", True, 11),
        ("Every matched row, filterable. Includes total contract value (TCV) per tier,", False, 10),
        ("exact vs estimated flag, region used, and the synthesis note.", False, 10),
        ("", False, 10),
        ("Assumptions", True, 11),
        ("FX: USD/INR = 94.61 (edit USD_INR in match_competitors.py).", False, 10),
        ("OS: Ubuntu / Linux on-demand only. Neysa VMs are Ubuntu, so competitors", False, 10),
        ("     are compared on their Linux pricing. Windows / other-OS SKUs are kept", False, 10),
        ("     in the data but excluded from headline matching. Set OS_FILTER=None", False, 10),
        ("     in match_competitors.py to compare across all operating systems.", False, 10),
        ("Billing month = 730 hours. Neysa committed prices are monthly INR in the", False, 10),
        ("source sheet, converted to USD per GPU per hour for comparison.", False, 10),
        ("Region priority: India > US > global. A100 excluded (Neysa does not sell it).", False, 10),
        ("GPU ladder for nearest-match: L4 < L40S < H100 NVL ~ H100 SXM < H200 < B200 < B300.", False, 10),
    ]
    for i, (txt, bold, size) in enumerate(lines, 1):
        c = ws.cell(i, 1, txt)
        c.font = Font(bold=bold, size=size, name="Arial",
                      color="1F2A44" if bold else "000000")
    ws.column_dimensions["A"].width = 95
