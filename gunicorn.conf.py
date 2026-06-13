import os
from pathlib import Path

BASE = Path(__file__).parent

bind        = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers     = 1          # SQLite no tolera múltiples procesos escritores
threads     = 4          # concurrencia via threads
worker_class = "gthread"
timeout     = 120
keepalive   = 5
preload_app = True

accesslog = str(BASE / "logs" / "access.log")
errorlog  = str(BASE / "logs" / "error.log")
loglevel  = "info"
