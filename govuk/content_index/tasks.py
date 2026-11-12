"""The content index's background work, as Django tasks.

With the immediate backend, the only one configured (``TASKS`` unset), a task
runs as soon as it is enqueued, in the same process; with a worker backend
the same code runs off the request. The backend catches a task's exceptions
either way.
"""

from django.tasks import task

from .harvest import harvest_source


@task
def harvest_source_task(url: str) -> None:
    harvest_source(url)
