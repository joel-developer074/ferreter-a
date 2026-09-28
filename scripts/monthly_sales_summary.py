"""Genera un resumen CSV del mes anterior a partir de ventas confirmadas."""

import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import Sale, create_app, db

# Usar la fecha local, igual que las fechas de venta guardadas por la app.
today = datetime.now(timezone.utc).astimezone().date()
first_current = today.replace(day=1)
last_month = first_current - timedelta(days=1)
month_start = last_month.replace(day=1)

app = create_app()
with app.app_context():
    total, count = db.session.query(func.coalesce(func.sum(Sale.total), 0), func.count(Sale.id)).filter(
        Sale.status == "CONFIRMADA", Sale.created_at >= month_start, Sale.created_at < first_current
    ).one()
    destination = Path(app.instance_path) / "resumenes"
    destination.mkdir(exist_ok=True)
    output = destination / f"ventas-{month_start:%Y-%m}.csv"
    with output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["Periodo", "Ventas confirmadas", "Total ARS"])
        writer.writerow([month_start.strftime("%Y-%m"), count, f"{total:.2f}"])
    print(f"Resumen creado: {output}")
