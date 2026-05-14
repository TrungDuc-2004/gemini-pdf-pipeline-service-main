import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_env: str
    app_host: str
    app_port: int
    workspace_dir: Path
    output_dir: Path
    log_dir: Path
    gemini_model: str
    gemini_cooldown_seconds: int
    gemini_max_wait_seconds: int
    mongo_uri: str
    mongo_db_name: str
    allow_old_metadata_db_write: bool
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    minio_secure: bool
    minio_public_url: str
    enable_kaggle: bool
    kaggle_username: str
    kaggle_key: str
    kaggle_kernel_ref: str
    kaggle_dataset_id: str
    kaggle_max_attempts: int
    kaggle_poll_seconds: int
    postgres_dsn: str
    pg_host: str
    pg_port: str
    pg_user: str
    pg_password: str
    pg_name: str
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str
    e5_model_name: str


@lru_cache
def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "gemini-pdf-pipeline-service"),
        app_env=os.getenv("APP_ENV", "development"),
        app_host=os.getenv("APP_HOST", "0.0.0.0"),
        app_port=int(os.getenv("APP_PORT", "8100")),
        workspace_dir=Path(os.getenv("WORKSPACE_DIR", "./workspace")),
        output_dir=Path(os.getenv("OUTPUT_DIR", "./output")),
        log_dir=Path(os.getenv("LOG_DIR", "./logs")),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        gemini_cooldown_seconds=int(os.getenv("GEMINI_COOLDOWN_SECONDS", "300")),
        gemini_max_wait_seconds=int(os.getenv("GEMINI_MAX_WAIT_SECONDS", "300")),
        mongo_uri=os.getenv("MONGO_URI", "mongodb://localhost:27017"),
        mongo_db_name=os.getenv("MONGO_DB_NAME", "data-ai-tra-cuu"),
        allow_old_metadata_db_write=os.getenv("ALLOW_OLD_METADATA_DB_WRITE", "false").lower() in {"1", "true", "yes", "on"},
        minio_endpoint=os.getenv("MINIO_ENDPOINT", "http://127.0.0.1:9000"),
        minio_access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        minio_secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        minio_bucket=os.getenv("MINIO_BUCKET", "ai-tra-cuu"),
        minio_secure=os.getenv("MINIO_SECURE", "false").lower() in {"1", "true", "yes", "on"},
        minio_public_url=os.getenv("MINIO_PUBLIC_URL", "http://127.0.0.1:9000"),
        enable_kaggle=os.getenv("ENABLE_KAGGLE", "false").lower() in {"1", "true", "yes", "on"},
        kaggle_username=os.getenv("KAGGLE_USERNAME", ""),
        kaggle_key=os.getenv("KAGGLE_KEY", ""),
        kaggle_kernel_ref=os.getenv("KAGGLE_KERNEL_REF", "dat261303/debug-cutlines-auto"),
        kaggle_dataset_id=os.getenv("KAGGLE_DATASET_ID", "dat261303/kaggle-pack"),
        kaggle_max_attempts=int(os.getenv("KAGGLE_MAX_ATTEMPTS", "3")),
        kaggle_poll_seconds=int(os.getenv("KAGGLE_POLL_SECONDS", "20")),
        postgres_dsn=os.getenv("POSTGRES_DSN", ""),
        pg_host=os.getenv("PG_HOST", "localhost"),
        pg_port=os.getenv("PG_PORT", "5432"),
        pg_user=os.getenv("PG_USER", "postgres"),
        pg_password=os.getenv("PG_PASSWORD", "postgres"),
        pg_name=os.getenv("PG_NAME", "data_ai_tra_cuu"),
        neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
        neo4j_password=os.getenv("NEO4J_PASSWORD", "password"),
        neo4j_database=os.getenv("NEO4J_DATABASE", ""),
        e5_model_name=os.getenv("E5_MODEL_NAME", "intfloat/multilingual-e5-base"),
    )


_BLOCKED_METADATA_DB_NAMES = {
    "data-khoa-luan",
    "metadata-edu",
    "metadata_edu",
}


def validate_safe_mongo_db_name(db_name: str | None = None) -> str:
    settings = get_settings()
    target = (db_name or settings.mongo_db_name).strip()
    if target.lower() in _BLOCKED_METADATA_DB_NAMES and not settings.allow_old_metadata_db_write:
        raise RuntimeError("Refusing to write to old Metadata-Edu database. Use MONGO_DB_NAME=data-ai-tra-cuu.")
    return target
