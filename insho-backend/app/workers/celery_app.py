import os

from celery import Celery

# Redis database 1: task broker
# Redis database 2: task results
BROKER_URL = os.getenv(
    "CELERY_BROKER_URL",
    "redis://localhost:6379/1"
)

RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND",
    "redis://localhost:6379/2"
)

celery_app = Celery(
    "insho",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Time configuration
    timezone="UTC",
    enable_utc=True,

    # Task tracking
    task_track_started=True,
    result_expires=3600,

    # Worker behavior
    worker_prefetch_multiplier=1,
    worker_concurrency=int(os.getenv("CELERY_WORKER_CONCURRENCY", "2")),
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_publish_retry=False,
    broker_transport_options={"socket_connect_timeout": 2, "socket_timeout": 2},

    # Reliability
    broker_connection_retry_on_startup=True,

    # Default queue
    task_default_queue="insho",
)

celery_app.autodiscover_tasks(["app.workers"])
