import logging
from pathlib import Path

from app.core.paths import service_log_root
from app.utils.files import ensure_dir


def configure_logging() -> None:
    ensure_dir(service_log_root())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(service_log_root() / "service.log", encoding="utf-8"),
        ],
    )


def append_job_log(log_path: Path, message: str) -> None:
    ensure_dir(log_path.parent)
    with log_path.open("a", encoding="utf-8") as file:
        file.write(message.rstrip() + "\n")

