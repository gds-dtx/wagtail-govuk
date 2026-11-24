"""ASGI service under uvicorn, for slow requests that would tie up gunicorn workers.

Features contribute routes via ``settings.SERVICE_CONTRIBUTIONS`` and never import one another.
"""

HEALTH_PATH = "/service/health"
