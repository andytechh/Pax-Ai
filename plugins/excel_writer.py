"""
plugins/excel_writer.py — build and edit real .xlsx spreadsheets by voice.

"Build a spreadsheet of this month's expenses", "add a row to my budget file".
Give it the data directly, or describe what you want and it drafts the table
with the model. Formulas (=SUM(B2:B9)) are written as formulas, numbers as
numbers, and the header row is styled and frozen.
"""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN = {
    "name": "excel_writer",
    "tier": "extra",
    "description": (
        "Create or extend an Excel spreadsheet (.xlsx). Use for: 'make a spreadsheet of…', 'build "
        "an expense sheet', 'add a row to budget.xlsx', 'put this in Excel'. Pass headers+rows if "
        "the data is known; otherwise pass `describe` and the table is drafted. Saves to the "
        "Documents folder unless a folder is named. Do NOT use to read or analyse an existing "
        "spreadsheet (file_processor does that)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "filename": {"type": "STRING", "description": "File name, e.g. expenses.xlsx (the .xlsx is added)"},
            "folder": {"type": "STRING", "description": "Optional folder; default Documents"},
            "sheet": {"type": "STRING", "description": "Sheet name (default 'Sheet1')"},
            "headers": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Column titles"},
            "rows": {"type": "ARRAY", "items": {"type": "ARRAY", "items": {"type": "STRING"}},
                     "description": "Rows of cells. Numbers as digits, formulas starting with ="},
            "describe": {"type": "STRING", "description": "If no rows are given: what the sheet is for"},
            "mode": {"type": "STRING", "description": "create (default) | append (add rows to an existing file)"},
            "totals": {"type": "BOOLEAN", "description": "Add a SUM row under every numeric column"},
        },
        "required": ["filename"],
    },
}


def _cell(v):
    if not isinstance(v, str):
        return v
    s = v.strip()
    if s.startswith("="):
        return s
    plain = s.replace(",", "") if re.fullmatch(r"-?[\d,]+(\.\d+)?", s) else s
    try:
        return int(plain) if re.fullmatch(r"-?\d+", plain) else float(plain)
    except ValueError:
        return s


def _draft(describe: str) -> tuple[list, list]:
    from core import gemini
    data = gemini.as_json(
        "Design a small spreadsheet for this request and return ONLY JSON of the form "
        '{"headers": ["..."], "rows": [["..."]]}. Use realistic placeholder rows (at most 12), '
        "numbers as plain digits, and formulas like =B2*C2 where a column is computed. Request: "
        + describe, tier=gemini.SMART, timeout_ms=45_000)
    if not isinstance(data, dict) or not data.get("headers"):
        raise RuntimeError("the model did not return a usable table")
    return [str(h) for h in data["headers"]], [list(r) for r in (data.get("rows") or [])]


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        from openpyxl import Workbook, load_workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        return "Excel support needs openpyxl — run: pip install openpyxl"
    try:
        name = re.sub(r'[<>:"/\\|?*]', "_", str(parameters.get("filename") or "sheet").strip()) or "sheet"
        if not name.lower().endswith(".xlsx"):
            name += ".xlsx"
        folder = Path(str(parameters.get("folder") or "").strip() or (Path.home() / "Documents")).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        headers = [str(h) for h in (parameters.get("headers") or [])]
        rows = [list(r) for r in (parameters.get("rows") or [])]
        mode = str(parameters.get("mode") or "create").lower()
        if not headers and not rows:
            if not parameters.get("describe"):
                return "Tell me what the spreadsheet is for, or give me the columns and rows."
            headers, rows = _draft(str(parameters["describe"]))

        if mode == "append" and path.exists():
            wb = load_workbook(path)
            ws = wb[parameters.get("sheet")] if parameters.get("sheet") in wb.sheetnames else wb.active
            for r in rows:
                ws.append([_cell(c) for c in r])
            wb.save(path)
            msg = f"Added {len(rows)} row(s) to {path.name}."
        else:
            if path.exists():
                stem, n = path.stem, 2
                while path.exists():
                    path = folder / f"{stem} ({n}).xlsx"
                    n += 1
            wb = Workbook()
            ws = wb.active
            ws.title = str(parameters.get("sheet") or "Sheet1")[:31]
            if headers:
                ws.append(headers)
                for c in ws[1]:
                    c.font = Font(bold=True, color="FFFFFF")
                    c.fill = PatternFill("solid", fgColor="1F4E78")
                    c.alignment = Alignment(horizontal="center")
                ws.freeze_panes = "A2"
            for r in rows:
                ws.append([_cell(c) for c in r])
            if parameters.get("totals") and rows:
                last = ws.max_row
                total = ["Total"] + [""] * (max(len(headers), 1) - 1)
                for ci in range(2, len(headers) + 1):
                    col = get_column_letter(ci)
                    if any(isinstance(ws.cell(r, ci).value, (int, float)) or
                           str(ws.cell(r, ci).value or "").startswith("=") for r in range(2, last + 1)):
                        total[ci - 1] = f"=SUM({col}2:{col}{last})"
                ws.append(total)
                for c in ws[ws.max_row]:
                    c.font = Font(bold=True)
            for ci, col in enumerate(ws.columns, 1):
                width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                ws.column_dimensions[get_column_letter(ci)].width = min(max(width + 2, 10), 50)
            wb.save(path)
            msg = f"Created {path.name} with {len(rows)} row(s) in {path.parent}."
        if player:
            player.write_log(f"FILE: Excel — {path}")
        return msg
    except PermissionError:
        return "I couldn't save it — is that file open in Excel? Close it and ask again."
    except Exception as e:
        return f"Excel writer failed: {e}"
