import sys
import os
from pathlib import Path

project_home = Path(__file__).parent
if str(project_home) not in sys.path:
    sys.path.insert(0, str(project_home))

# Cargar variables de .env
env_file = project_home / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from app import app as application  # noqa: E402
