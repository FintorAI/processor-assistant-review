#!/usr/bin/env python3
"""sheet_sync - keep the processor checklist Google Sheet in step with local edits.

The Google Sheet (SPREADSHEET_ID) is the canonical checklist. This script lets the
build loop push per-item status updates into it WITHOUT clobbering manual edits:
it indexes rows by (Section number, Item #) and writes only the status columns of
the matched row via values.batchUpdate. The Questions column and any manual columns
are never touched.

Auth: a Google service-account key. Path comes from the env var SHEET_SYNC_SA_KEY,
defaulting to ~/.config/gcp/processor-assistant-sheets.json. The sheet must be shared
with the service-account email as Editor and the Sheets API enabled on the project.

Usage:
    # download the sheet to the local working CSV
    python scripts/sheet_sync.py pull --out docs/processor_checklist_code_reality.csv

    # push one or more row updates (JSON list on stdin or via --json FILE)
    echo '[{"section": 1, "item": "4", "Status": "Implemented",
            "Notes": "buyer-name vs pa_buyer_name",
            "Update Log": "2026-07-01 built 1.4"}]' \
        | python scripts/sheet_sync.py push

If the key file is absent the script no-ops with a clear message (exit 0) so the
build loop is never blocked - fall back to pasting changes into the sheet manually.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

SPREADSHEET_ID = "1EFHvUhynDQsfPBI7fQnmJVKD5oIGWgyMeBi7_QPx5Fo"
DEFAULT_KEY_PATH = os.path.expanduser("~/.config/gcp/processor-assistant-sheets.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Columns the agent is allowed to write. Everything else (notably "Questions")
# stays under human control and is never modified by a push.
WRITABLE_COLUMNS = {"Code Artifact", "Status", "Notes", "Last updated", "Update Log"}
# Stamped automatically on every push unless the caller supplies them.
_AUTO_STAMP = "Last updated"


def _key_path() -> str:
    return os.environ.get("SHEET_SYNC_SA_KEY", DEFAULT_KEY_PATH)


def _col_letter(idx0: int) -> str:
    """0-based column index -> A1 column letter (A, B, ..., Z, AA, ...)."""
    letters = ""
    n = idx0 + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _section_number(section: str) -> Optional[int]:
    m = re.match(r"\s*(\d+)", section or "")
    return int(m.group(1)) if m else None


def _norm_item(item: Any) -> str:
    return str(item).strip()


class SheetSync:
    def __init__(self, service, sheet_title: str, header: List[str], rows: List[List[str]]):
        self.service = service
        self.sheet_title = sheet_title
        self.header = header
        self.rows = rows
        self.col_index = {name: i for i, name in enumerate(header)}
        try:
            self._sec_col = self.col_index["Section"]
            self._item_col = self.col_index["Item #"]
        except KeyError as exc:  # pragma: no cover - sheet shape guard
            raise SystemExit(f"Sheet is missing required column {exc}. Header={header}")
        # (section_number, item#) -> 1-based sheet row number (row 1 is the header)
        self.index: Dict[Tuple[int, str], int] = {}
        for offset, row in enumerate(rows):
            sec = _section_number(row[self._sec_col] if self._sec_col < len(row) else "")
            item = _norm_item(row[self._item_col]) if self._item_col < len(row) else ""
            if sec is not None and item:
                self.index[(sec, item)] = offset + 2  # +2: header + 1-based

    # ── construction ────────────────────────────────────────────────
    @classmethod
    def connect(cls) -> "SheetSync":
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(_key_path(), scopes=SCOPES)
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
        sheet_title = meta["sheets"][0]["properties"]["title"]
        resp = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=SPREADSHEET_ID, range=sheet_title)
            .execute()
        )
        values = resp.get("values", [])
        if not values:
            raise SystemExit("Sheet returned no rows.")
        header, rows = values[0], values[1:]
        return cls(service, sheet_title, header, rows)

    # ── read ────────────────────────────────────────────────────────
    def to_csv(self, dest: str) -> int:
        width = len(self.header)
        with open(dest, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(self.header)
            for row in self.rows:
                w.writerow((row + [""] * width)[:width])
        return len(self.rows)

    # ── write-by-key ─────────────────────────────────────────────────
    def push(self, updates: List[Dict[str, Any]]) -> Tuple[int, List[str]]:
        """Apply per-item column updates. Returns (cells_written, warnings)."""
        data: List[Dict[str, Any]] = []
        warnings: List[str] = []
        today = _dt.date.today().isoformat()
        for upd in updates:
            sec = upd.get("section")
            if sec is None:
                sec = _section_number(str(upd.get("Section", "")))
            item = _norm_item(upd.get("item", upd.get("Item #", "")))
            row_no = self.index.get((int(sec), item)) if sec is not None else None
            if not row_no:
                warnings.append(f"no row for section={sec} item={item!r}")
                continue
            cols = {k: v for k, v in upd.items() if k in WRITABLE_COLUMNS}
            if _AUTO_STAMP not in cols and _AUTO_STAMP in self.col_index:
                cols[_AUTO_STAMP] = today
            for name, val in cols.items():
                ci = self.col_index.get(name)
                if ci is None:
                    warnings.append(f"column {name!r} not in sheet; skipped")
                    continue
                a1 = f"{self.sheet_title}!{_col_letter(ci)}{row_no}"
                data.append({"range": a1, "values": [[val]]})
        if not data:
            return 0, warnings
        body = {"valueInputOption": "USER_ENTERED", "data": data}
        res = (
            self.service.spreadsheets()
            .values()
            .batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body)
            .execute()
        )
        return res.get("totalUpdatedCells", 0), warnings


def _load_updates(path: Optional[str]) -> List[Dict[str, Any]]:
    raw = open(path).read() if path else sys.stdin.read()
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise SystemExit("push payload must be a JSON object or list of objects")
    return data


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_pull = sub.add_parser("pull", help="download the sheet to a local CSV")
    p_pull.add_argument("--out", default="docs/processor_checklist_code_reality.csv")
    p_push = sub.add_parser("push", help="write per-item status updates to the sheet")
    p_push.add_argument("--json", dest="json_path", default=None,
                        help="path to a JSON list of updates (default: stdin)")
    args = parser.parse_args(argv)

    if not os.path.exists(_key_path()):
        print(
            f"[sheet_sync] no service-account key at {_key_path()} "
            "(set SHEET_SYNC_SA_KEY). Skipping sheet sync - update the sheet manually.",
            file=sys.stderr,
        )
        return 0

    sync = SheetSync.connect()

    if args.cmd == "pull":
        n = sync.to_csv(args.out)
        print(f"[sheet_sync] pulled {n} rows -> {args.out}")
        return 0

    if args.cmd == "push":
        updates = _load_updates(args.json_path)
        cells, warnings = sync.push(updates)
        for w in warnings:
            print(f"[sheet_sync] WARN: {w}", file=sys.stderr)
        print(f"[sheet_sync] updated {cells} cell(s) across {len(updates)} item(s)")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
