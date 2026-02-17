import os
from typing import Optional

import firebase_admin
from firebase_admin import credentials, firestore


def _resolve_service_account_path() -> Optional[str]:
    """
    Resolve a local service-account file path for development, if provided.

    Resolution order:
    1) FIREBASE_SERVICE_ACCOUNT_PATH
    2) GOOGLE_APPLICATION_CREDENTIALS
    """
    for env_key in ("FIREBASE_SERVICE_ACCOUNT_PATH", "GOOGLE_APPLICATION_CREDENTIALS"):
        candidate = os.getenv(env_key)
        if candidate and os.path.exists(candidate):
            return candidate
    return None


if not firebase_admin._apps:
    service_account_path = _resolve_service_account_path()
    if service_account_path:
        firebase_admin.initialize_app(credentials.Certificate(service_account_path))
    else:
        # Uses Application Default Credentials in managed environments (e.g. Cloud Run).
        firebase_admin.initialize_app()

db = firestore.client()
