"""Constantes de l'intégration Toque."""

from __future__ import annotations

DOMAIN = "toque"

CONF_TOKEN = "token"
DEFAULT_PORT = 8765
API_PREFIX = "/api/v1"
SUPPORTED_API = 1

# Règle de fraîcheur (spec §3, /state)
MAX_SNAPSHOT_AGE_MS = 5000
# Signal de vie du robot : 5 s pendant un travail, 30 s sinon. Indisponible après 2x.
HEARTBEAT_JOB_S = 5
HEARTBEAT_IDLE_S = 30
# Repli : interrogation de /state si le flux SSE se tait.
POLL_INTERVAL_S = 60
# Fréquence de réévaluation de la disponibilité.
AVAILABILITY_CHECK_S = 5

EVENT_TYPES = ["step", "action_requise", "done", "job_done", "fault"]

SERVICE_LIST_RECIPES = "list_recipes"
SERVICE_GET_RECIPE = "get_recipe"
SERVICE_ADD_TO_TODO = "add_recipe_to_todo"
