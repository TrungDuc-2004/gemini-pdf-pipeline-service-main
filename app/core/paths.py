from pathlib import Path

from app.core.config import get_settings


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_project_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return project_root() / path


def workspace_root() -> Path:
    return resolve_project_path(get_settings().workspace_dir)


def gemini_rotation_state_path() -> Path:
    return workspace_root() / "gemini_rotation_state.json"


def output_root() -> Path:
    return resolve_project_path(get_settings().output_dir)


def kaggle_pack_root() -> Path:
    return project_root() / "kaggle_pack"


def kaggle_output_root(kernel_slug: str) -> Path:
    return output_root() / "_kaggle_outputs" / kernel_slug


def service_log_root() -> Path:
    return resolve_project_path(get_settings().log_dir)


def job_workspace(job_id: str) -> Path:
    return workspace_root() / job_id


def job_logs_dir(job_id: str) -> Path:
    return job_workspace(job_id) / "logs"


def job_log_path(job_id: str) -> Path:
    return job_logs_dir(job_id) / "job.log"


def job_config_path(job_id: str) -> Path:
    return job_workspace(job_id) / "job_config.json"


def job_state_path(job_id: str) -> Path:
    return job_workspace(job_id) / "job_state.json"


def job_progress_path(job_id: str) -> Path:
    return job_workspace(job_id) / "progress.json"


def job_result_path(job_id: str) -> Path:
    return job_workspace(job_id) / "result.json"


def job_source_pdf_path(job_id: str) -> Path:
    return job_workspace(job_id) / "source.pdf"
