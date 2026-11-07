"""
scheduler.py — APScheduler para descargas automáticas programadas.
"""
import logging
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class DropiScheduler:
    """
    Wraps APScheduler para programar descargas automáticas en horarios configurables.

    Args:
        on_trigger: Función sin argumentos llamada cada vez que dispara un horario.
    """

    def __init__(self, on_trigger: Callable[[], None]) -> None:
        self.on_trigger = on_trigger
        self._jobs: dict = {}
        self._scheduler = BackgroundScheduler(daemon=True)
        self._scheduler.start()
        logger.info("DropiScheduler iniciado.")

    def update_schedule(self, horarios: dict) -> None:
        """
        Actualiza los jobs según el diccionario de horarios.

        Args:
            horarios: {"08:00": True, "12:00": False, ...}
        """
        # Eliminar jobs anteriores
        for job_id in list(self._jobs.keys()):
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass
        self._jobs.clear()

        # Agregar nuevos jobs
        for time_str, enabled in horarios.items():
            if not enabled:
                continue
            try:
                hour, minute = map(int, time_str.split(":"))
                job_id = f"dropi_{time_str.replace(':', '')}"
                job = self._scheduler.add_job(
                    self.on_trigger,
                    CronTrigger(hour=hour, minute=minute),
                    id=job_id,
                    replace_existing=True,
                    misfire_grace_time=300,
                )
                self._jobs[job_id] = job
                logger.info(f"Job programado: {time_str}")
            except Exception as exc:
                logger.error(f"Error al programar {time_str}: {exc}")

    def get_active_times(self) -> list:
        """Retorna la lista de horarios activos como strings."""
        times = []
        for job_id in self._jobs:
            times.append(job_id.replace("dropi_", "")[:2] + ":" +
                         job_id.replace("dropi_", "")[2:])
        return times

    def shutdown(self) -> None:
        try:
            self._scheduler.shutdown(wait=False)
        except Exception:
            pass
