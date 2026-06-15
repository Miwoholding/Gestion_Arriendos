import shutil
from datetime import datetime
from pathlib import Path

BASE    = Path(__file__).parent
DB_FILE = BASE / "gestion_arriendos.db"
BACKUP_DIR = BASE / "backups"
BACKUP_DIR.mkdir(exist_ok=True)

timestamp = datetime.now().strftime("%Y%m%d_%H%M")
destino   = BACKUP_DIR / f"gestion_arriendos_{timestamp}.db"

shutil.copy2(DB_FILE, destino)
print(f"Backup creado: {destino.name}")

# Conservar solo los últimos 30 backups
backups = sorted(BACKUP_DIR.glob("*.db"))
for viejo in backups[:-30]:
    viejo.unlink()
    print(f"Eliminado backup antiguo: {viejo.name}")
