"""Copia local de seguridad para la instalación SQLite de FerreSoft.

Programar una vez al día mediante el Programador de tareas de Windows.
Para MySQL en producción, reemplazar esta tarea por un backup consistente
con mysqldump o con el servicio administrado elegido.
"""

import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "instance" / "ferresoft.db"
DESTINATION = ROOT / "backups"

if not SOURCE.exists():
    raise SystemExit(f"No se encontró la base de datos: {SOURCE}")

DESTINATION.mkdir(exist_ok=True)
today = datetime.now(timezone.utc).astimezone().date()
target = DESTINATION / f"ferresoft-{today:%Y-%m-%d}.db"
shutil.copy2(SOURCE, target)
print(f"Backup creado: {target}")
