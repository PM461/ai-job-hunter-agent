import json
import logging
from pathlib import Path
from typing import Dict, Any

BASE_DIR = Path(__file__).resolve().parent.parent
MAPPING_FILE = BASE_DIR / "data" / "career_sites_mapping.json"

logger = logging.getLogger("JobAgent.SiteCatalog")


def get_mapped_sites() -> Dict[str, Any]:
    if not MAPPING_FILE.exists():
        return {}
    try:
        with open(MAPPING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Errore caricamento catalog siti: {e}")
        return {}

