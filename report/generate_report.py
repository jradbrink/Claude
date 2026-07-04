#!/usr/bin/env python3
"""Black Box V1 — owner report PDF generator.

Reads trips from Supabase (default) or straight from the device's SQLite
buffer (--local-db), aggregates them, and renders a professional PDF meant
for insurance, inspection or a sale.

Usage:
    # From Supabase (env: SUPABASE_URL, SUPABASE_SERVICE_KEY)
    python generate_report.py --vehicle-id <uuid> --out report.pdf

    # Straight from the Pi's local database
    python generate_report.py --local-db blackbox.db \
        --vehicle-name "Porsche 996.2 C4S" --vin WP0ZZZ99Z2S600000 --out report.pdf

Optional: --from 2026-01-01 --to 2026-06-30 to limit the period.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone

import requests
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ---------------------------------------------------------------- palette --
# Validated single-hue chart palette on a light (print) surface.
INK = colors.HexColor("#0b0b0b")
INK_SOFT = colors.HexColor("#52514e")
INK_MUTED = colors.HexColor("#8b8a84")
SERIES = colors.HexColor("#2a78d6")
GRID = colors.HexColor("#e8e7e2")
RULE = colors.HexColor("#d6d5cf")
ROW_LINE = colors.HexColor("#eceae4")

MONTHS_SV = ["jan", "feb", "mar", "apr", "maj", "jun",
             "jul", "aug", "sep", "okt", "nov", "dec"]


# ------------------------------------------------------------------- data --

@dataclass
class Vehicle:
    display_name: str
    vin: str | None = None
    make: str | None = None
    model: str | None = None
    model_year: int | None = None


def fetch_supabase(vehicle_id: str) -> tuple[Vehicle, list[dict]]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY (or use --local-db).")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    resp = requests.get(
        f"{url}/rest/v1/vehicles",
        params={"id": f"eq.{vehicle_id}", "select": "*"},
        headers=headers, timeout=15,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        sys.exit(f"No vehicle with id {vehicle_id}")
    v = rows[0]
    vehicle = Vehicle(
        display_name=v["display_name"], vin=v.get("vin"),
        make=v.get("make"), model=v.get("model"), model_year=v.get("model_year"),
    )

    trips: list[dict] = []
    offset = 0
    while True:
        resp = requests.get(
            f"{url}/rest/v1/trips",
            params={
                "vehicle_id": f"eq.{vehicle_id}",
                "select": "*",
                "order": "started_at.asc",
                "offset": str(offset),
                "limit": "1000",
            },
            headers=headers, timeout=15,
        )
        resp.raise_for_status()
        page = resp.json()
        trips.extend(page)
        if len(page) < 1000:
            return vehicle, trips
        offset += 1000


def fetch_local(db_path: str, name: str, vin: str | None) -> tuple[Vehicle, list[dict]]:
    db = sqlite3.connect(db_path)
    rows = db.execute("SELECT payload FROM trips ORDER BY created_at").fetchall()
    trips = sorted(
        (json.loads(r[0]) for r in rows), key=lambda t: t["started_at"]
    )
    return Vehicle(display_name=name, vin=vin), trips


# ------------------------------------------------------------------ stats --

def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass
class Stats:
    n_trips: int
    total_km: float
    total_hours: float
    clean_share: float | None  # share of trips with zero cold violations
    last_driven: datetime | None
    median_warmup_min: float | None
    monthly: list[tuple[str, int]]  # last 12 months, oldest first


def month_key(dt: datetime) -> tuple[int, int]:
    return (dt.year, dt.month)


def compute_stats(trips: list[dict], today: date) -> Stats:
    n = len(trips)
    total_km = sum(float(t.get("distance_km_est") or 0) for t in trips)
    total_hours = sum(int(t.get("duration_s") or 0) for t in trips) / 3600
    clean = sum(1 for t in trips if not t.get("cold_violation_count"))
    warmups = [t["warmup_s"] for t in trips if t.get("warmup_s") is not None]

    counts: dict[tuple[int, int], int] = {}
    for t in trips:
        counts[month_key(parse_ts(t["started_at"]))] = (
            counts.get(month_key(parse_ts(t["started_at"])), 0) + 1
        )
    monthly: list[tuple[str, int]] = []
    year, month = today.year, today.month
    keys: list[tuple[int, int]] = []
    for _ in range(12):
        keys.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    for y, m in reversed(keys):
        label = MONTHS_SV[m - 1] + (f" {y % 100:02d}" if m == 1 or (y, m) == keys[-1] else "")
        monthly.append((label, counts.get((y, m), 0)))

    return Stats(
        n_trips=n,
        total_km=total_km,
        total_hours=total_hours,
        clean_share=(clean / n) if n else None,
        last_driven=parse_ts(trips[-1]["started_at"]) if trips else None,
        median_warmup_min=(statistics.median(warmups) / 60) if warmups else None,
        monthly=monthly,
    )


# ------------------------------------------------------------------ chart --

def nice_ceiling(value: int, n_ticks: int = 4) -> int:
    """Smallest multiple of n_ticks >= value, so every gridline lands on an
    integer tick value."""
    value = max(value, n_ticks)
    return ((value + n_ticks - 1) // n_ticks) * n_ticks


def monthly_chart(monthly: list[tuple[str, int]], width: float, height: float) -> Drawing:
    d = Drawing(width, height)
    left, right, top, bottom = 26, 4, 8, 16
    plot_w = width - left - right
    plot_h = height - top - bottom

    y_max = nice_ceiling(max((c for _, c in monthly), default=0))
    n_ticks = 4
    for i in range(n_ticks + 1):
        frac = i / n_ticks
        y = bottom + plot_h * frac
        tick_val = round(y_max * frac)
        if i > 0:  # baseline drawn separately
            d.add(Line(left, y, left + plot_w, y, strokeColor=GRID, strokeWidth=0.5))
        d.add(String(left - 5, y - 2.5, str(tick_val), fontName="Helvetica",
                     fontSize=6.5, fillColor=INK_MUTED, textAnchor="end"))

    slot = plot_w / len(monthly)
    bar_w = slot * 0.55
    for i, (label, count) in enumerate(monthly):
        x = left + i * slot + (slot - bar_w) / 2
        if count > 0:
            bar_h = plot_h * count / y_max
            d.add(Rect(x, bottom, bar_w, bar_h, fillColor=SERIES,
                       strokeColor=colors.white, strokeWidth=0.75))
        d.add(String(left + i * slot + slot / 2, bottom - 9, label,
                     fontName="Helvetica", fontSize=6.5, fillColor=INK_SOFT,
                     textAnchor="middle"))

    d.add(Line(left, bottom, left + plot_w, bottom, strokeColor=RULE, strokeWidth=0.75))
    return d


# -------------------------------------------------------------------- pdf --

STYLES = {
    "kicker": ParagraphStyle(
        "kicker", fontName="Helvetica-Bold", fontSize=8, leading=10,
        textColor=INK_MUTED, spaceAfter=2,
    ),
    "title": ParagraphStyle(
        "title", fontName="Helvetica-Bold", fontSize=21, leading=25, textColor=INK,
    ),
    "meta": ParagraphStyle(
        "meta", fontName="Helvetica", fontSize=8.5, leading=12, textColor=INK_SOFT,
    ),
    "h2": ParagraphStyle(
        "h2", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=INK,
        spaceBefore=16, spaceAfter=6,
    ),
    "stat_value": ParagraphStyle(
        "stat_value", fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=INK,
    ),
    "stat_label": ParagraphStyle(
        "stat_label", fontName="Helvetica", fontSize=6.8, leading=9,
        textColor=INK_MUTED,
    ),
    "foot": ParagraphStyle(
        "foot", fontName="Helvetica", fontSize=7, leading=9.5, textColor=INK_MUTED,
    ),
}


def fmt_duration(seconds: int) -> str:
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def fmt_date(dt: datetime) -> str:
    return dt.astimezone().strftime("%Y-%m-%d")


def fmt_time(dt: datetime) -> str:
    return dt.astimezone().strftime("%H:%M")


def stat_cell(value: str, label: str) -> list:
    return [Paragraph(value, STYLES["stat_value"]),
            Paragraph(label.upper(), STYLES["stat_label"])]


def build_pdf(
    out_path: str,
    vehicle: Vehicle,
    trips: list[dict],
    stats: Stats,
    period: tuple[date | None, date | None],
) -> None:
    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Ägarrapport – {vehicle.display_name}",
        author="Black Box V1",
    )
    content_w = A4[0] - 36 * mm
    story: list = []

    # Header
    story.append(Paragraph("ÄGARRAPPORT — FORDONSANVÄNDNING", STYLES["kicker"]))
    story.append(Paragraph(vehicle.display_name, STYLES["title"]))
    meta_bits = []
    if vehicle.vin:
        meta_bits.append(f"VIN: {vehicle.vin}")
    p_from, p_to = period
    if trips:
        first = fmt_date(parse_ts(trips[0]["started_at"]))
        last = fmt_date(parse_ts(trips[-1]["started_at"]))
        meta_bits.append(
            f"Period: {p_from.isoformat() if p_from else first} – "
            f"{p_to.isoformat() if p_to else last}"
        )
    meta_bits.append(
        "Genererad: " + datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    )
    story.append(Spacer(1, 3))
    story.append(Paragraph("  •  ".join(meta_bits), STYLES["meta"]))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=0.75, color=INK))
    story.append(Spacer(1, 14))

    # Key figures
    clean_txt = (
        f"{stats.clean_share * 100:.0f} %" if stats.clean_share is not None else "–"
    )
    warm_txt = (
        f"{stats.median_warmup_min:.0f} min" if stats.median_warmup_min else "–"
    )
    cells = [
        stat_cell(str(stats.n_trips), "Antal körningar"),
        stat_cell(f"{stats.total_km:,.0f} km".replace(",", " "), "Total sträcka (est.)"),
        stat_cell(f"{stats.total_hours:.1f} h", "Total körtid"),
        stat_cell(clean_txt, "Körningar utan kallstartsöverträdelse"),
        stat_cell(fmt_date(stats.last_driven) if stats.last_driven else "–", "Senast körd"),
        stat_cell(warm_txt, "Medianuppvärmningstid till 80 °C"),
    ]
    stat_table = Table(
        [cells[0:3], cells[3:6]],
        colWidths=[content_w / 3] * 3,
    )
    stat_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("LINEBELOW", (0, 0), (-1, 0), 0.4, ROW_LINE),
    ]))
    story.append(stat_table)

    # Monthly frequency chart
    story.append(Paragraph("Körningar per månad (senaste 12 månaderna)", STYLES["h2"]))
    story.append(monthly_chart(stats.monthly, content_w, 130))
    story.append(Spacer(1, 4))

    # Recent trips table
    story.append(Paragraph("Senaste körningar", STYLES["h2"]))
    header = ["Datum", "Start", "Varaktighet", "Sträcka (est.)",
              "Uppvärmning", "Max kylv.", "Kallstartsöverträdelser"]
    rows = [header]
    for t in list(reversed(trips))[:25]:
        started = parse_ts(t["started_at"])
        warm = f"{round(t['warmup_s'] / 60)} min" if t.get("warmup_s") is not None else "ej varm"
        viol = t.get("cold_violation_count") or 0
        rows.append([
            fmt_date(started),
            fmt_time(started),
            fmt_duration(int(t.get("duration_s") or 0)),
            f"{float(t.get('distance_km_est') or 0):.1f} km",
            warm,
            f"{float(t['max_coolant_c']):.0f} °C" if t.get("max_coolant_c") is not None else "–",
            "inga" if viol == 0 else str(viol),
        ])
    trip_table = Table(rows, colWidths=[
        content_w * w for w in (0.13, 0.09, 0.14, 0.14, 0.13, 0.12, 0.25)
    ], repeatRows=1)
    trip_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK_SOFT),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, ROW_LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
    ]))
    story.append(trip_table)
    if len(trips) > 25:
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"Visar de 25 senaste av {len(trips)} körningar. "
            "Samtliga körningar ingår i nyckeltalen ovan.", STYLES["foot"]))

    # Method note
    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=0.4, color=RULE))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Metodik: Samtliga uppgifter är automatiskt registrerade via fordonets "
        "OBD-II-uttag (motorvarvtal, kylvätsketemperatur, hastighet) utan manuell "
        "inmatning. En körning registreras från motorstart till motorstopp. "
        "Sträckor är uppskattade genom integrering av fordonets hastighetssignal. "
        "”Kallstartsöverträdelse” avser varvtal över 3 000 r/min innan kylvätskan "
        "nått 80 °C. Rapporten genererades med Black Box V1.",
        STYLES["foot"],
    ))

    doc.build(story)


# ------------------------------------------------------------------- main --

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vehicle-id", help="vehicles.id in Supabase")
    ap.add_argument("--local-db", help="Path to the device's SQLite database")
    ap.add_argument("--vehicle-name", default="Fordon", help="Used with --local-db")
    ap.add_argument("--vin", help="Used with --local-db")
    ap.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    ap.add_argument("--out", default="report.pdf")
    args = ap.parse_args()

    if args.local_db:
        vehicle, trips = fetch_local(args.local_db, args.vehicle_name, args.vin)
    elif args.vehicle_id:
        vehicle, trips = fetch_supabase(args.vehicle_id)
    else:
        ap.error("provide --vehicle-id (Supabase) or --local-db")

    d_from = date.fromisoformat(args.date_from) if args.date_from else None
    d_to = date.fromisoformat(args.date_to) if args.date_to else None
    if d_from:
        trips = [t for t in trips if parse_ts(t["started_at"]).date() >= d_from]
    if d_to:
        trips = [t for t in trips if parse_ts(t["started_at"]).date() <= d_to]

    stats = compute_stats(trips, today=d_to or date.today())
    build_pdf(args.out, vehicle, trips, stats, (d_from, d_to))
    print(f"Wrote {args.out}  ({len(trips)} trips)")


if __name__ == "__main__":
    main()
