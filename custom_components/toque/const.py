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
AVAILABILITY_CHECK_S = 2
# Silence du flux SSE : au-delà de signal de vie + cette marge, on interroge /state
# sans attendre le repli de 60 s (le flux peut se taire sans se fermer).
SILENCE_GRACE_S = 1
# Un flux sans octet (pings de 15 s compris) pendant ce délai est considéré mort :
# deux intervalles de ping, pour ne pas reconnecter à tort (chaque connexion
# occupe une place sur le robot, limité à 8).
STREAM_READ_TIMEOUT_S = 30

# États d'un travail pendant lesquels le temps s'écoule (`job.state`, en minuscules).
JOB_RUNNING_STATES = ("starting", "running")
# La fin prévue reste figée tant que la nouvelle estimation ne s'en écarte pas davantage
# (`remainingS` est tronqué à la seconde et chaque `state` arrive avec un retard variable).
JOB_END_TOLERANCE_S = 5

# Image de la recette : délai avant nouvel essai après un 503 sans `Retry-After`, après une
# autre erreur, et délai minimal avant de redemander quand le catalogue change
# (une synchronisation publie un événement `recipes` à chaque modification).
IMAGE_BUSY_RETRY_S = 5
IMAGE_ERROR_RETRY_S = 60
IMAGE_REFRESH_MIN_S = 60

EVENT_TYPES = ["step", "action_requise", "done", "job_done", "fault"]

SERVICE_LIST_RECIPES = "list_recipes"
SERVICE_GET_RECIPE = "get_recipe"
SERVICE_ADD_TO_TODO = "add_recipe_to_todo"
