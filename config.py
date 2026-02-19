import os
from datetime import datetime
from dotenv import load_dotenv

# Cargar variables de entorno desde archivo .env
load_dotenv()

# Bot Token (desde variable de entorno)
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN no está configurado en las variables de entorno")

# Database connection (desde variable de entorno)
DB_URL = os.getenv("DB_URL")
if not DB_URL:
    raise ValueError("DB_URL no está configurado en las variables de entorno")

# NOTA DE SEGURIDAD: Esta variable ya no se usa. Las contraseñas se generan aleatoriamente.
# Se mantiene por compatibilidad pero debe eliminarse en futuras versiones.
DEFAULT_ACCOUNT_PASSWORD = os.getenv("DEFAULT_ACCOUNT_PASSWORD", "")

# Admin IDs (desde variable de entorno)
# Admin IDs (desde variable de entorno)
super_admin_env = os.getenv("SUPER_ADMIN_ID", "0")
SUPER_ADMIN_IDS = [int(id.strip()) for id in super_admin_env.split(",") if id.strip()]

if not SUPER_ADMIN_IDS or (len(SUPER_ADMIN_IDS) == 1 and SUPER_ADMIN_IDS[0] == 0):
    raise ValueError("SUPER_ADMIN_ID no está configurado en las variables de entorno")

# Admin IDs adicionales (opcional)
additional_admins = os.getenv("ADDITIONAL_ADMIN_IDS", "")
ADMIN_IDS = SUPER_ADMIN_IDS.copy()
if additional_admins:
    ADMIN_IDS.extend([int(id.strip()) for id in additional_admins.split(",") if id.strip()])

# Environment
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# Default prices (también se almacenarán en la DB)
DEFAULT_EMBY_PRICES = {
    "ADMIN": {
        "1_screen": 0,
        "live_tv": 0,
        "2_screens": 0
    },
    "SUPERRESELLER": {
        "1_screen": 5000,
        "live_tv": 7000,
        "2_screens": 8000
    }
}

DEFAULT_JELLYFIN_PRICES = {
    "ADMIN": {
        "1_screen": 0,
        "3_screens": 0,
        "live_tv": 0,
        "2_screens_tv": 0
    },
    "SUPERRESELLER": {
        "1_screen": 3000,
        "3_screens": 7000, 
        "live_tv": 6000,
        "2_screens_tv": 10000
    }
}

# Default roles configuration
DEFAULT_ROLES = [
    {"name": "SUPER_ADMIN", "description": "Super Administrador con control total", "is_admin": True},
    {"name": "ADMIN", "description": "Administrador del sistema", "is_admin": True},
    {"name": "SUPERRESELLER", "description": "Revendedor premium con precios especiales", "is_admin": False}
]
