"""Dashboard API -- a read-only query service over storage (dashboard-api-implementation
plan). Serves the DASH §8 endpoints with the DASH §8.1 mode contract, across all three
datasource modes. No writes except `POST /v1/sessions/{id}/feedback` (R9), the one place a
user can talk back."""
from .config import ApiConfig, load_config
from .errors import ApiError, BadRequestError, NotFoundError

__all__ = [
    "ApiConfig",
    "ApiError",
    "BadRequestError",
    "NotFoundError",
    "load_config",
]
