from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import append_job_log
from app.core.paths import (
    job_log_path,
    job_workspace,
    kaggle_output_root,
    kaggle_pack_root,
    output_root,
    project_root,
)
from app.utils.files import ensure_dir, read_json, write_json
from app.utils.time import utc_now_iso


_EMBEDDED_REQUEST_PATTERN = re.compile(
    r"^_EMBEDDED_RUN_REQUEST_JSON = .*$",
    re.MULTILINE,
)


def kernel_slug() -> str:
    kernel_ref = get_settings().kaggle_kernel_ref
    return kernel_ref.split("/", 1)[1] if "/" in kernel_ref else kernel_ref


def kaggle_result_path(job_id: str) -> Path:
    return job_workspace(job_id) / "kaggle_result.json"


def build_skipped_kaggle_result(job_id: str, *, enabled: bool, reason: str) -> dict[str, Any]:
    now = utc_now_iso()
    result = {
        "enabled": enabled,
        "skipped": True,
        "status": "skipped",
        "request_id": None,
        "expected_book_stem": None,
        "attempts": 0,
        "output_zip": None,
        "applied": False,
        "failure_reason": None,
        "errors": [],
        "reason": reason,
        "started_at": now,
        "completed_at": now,
    }
    write_json(kaggle_result_path(job_id), result)
    return result


def run_kaggle_postprocess_for_job(job_id: str, book_stem: str, bundle_path: Path) -> dict[str, Any]:
    service = _KagglePostprocessRun(job_id=job_id, book_stem=book_stem, bundle_path=bundle_path)
    return service.run()


class _KagglePostprocessRun:
    def __init__(self, *, job_id: str, book_stem: str, bundle_path: Path) -> None:
        self.job_id = job_id
        self.book_stem = book_stem
        self.bundle_path = bundle_path
        self.settings = get_settings()
        self.slug = kernel_slug()
        self.pack_dir = kaggle_pack_root()
        self.kaggle_out_root = kaggle_output_root(self.slug)
        self.dl_dir = self.kaggle_out_root / "downloads"
        self.kernel_template_dir = project_root() / "app" / "services" / "kaggle_kernel" / self.slug
        self.kernel_work_dir = job_workspace(job_id) / "kaggle_kernel" / self.slug
        self.kaggle_log_path = job_workspace(job_id) / "logs" / "kaggle.log"
        self.bundle_log_path = job_workspace(job_id) / "logs" / "bundle.log"
        self.result: dict[str, Any] = {
            "enabled": True,
            "skipped": False,
            "status": "running",
            "request_id": None,
            "expected_book_stem": book_stem,
            "attempts": 0,
            "output_zip": None,
            "applied": False,
            "failure_reason": None,
            "errors": [],
            "started_at": utc_now_iso(),
            "completed_at": None,
        }

    def run(self) -> dict[str, Any]:
        self._write_result()
        try:
            self._validate_config()
            self._ensure_kaggle_cli()
            self._build_pack()
            self._push_dataset()
            self._wait_for_dataset_marker_ready()
            zip_path = self._run_kernel_with_retries()
            self._validate_zip_top_level(zip_path)
            applied_path = self._safe_extract_zip_to_output(zip_path)
            self.result.update(
                {
                    "status": "completed",
                    "output_zip": str(zip_path),
                    "applied": True,
                    "applied_path": str(applied_path),
                    "completed_at": utc_now_iso(),
                }
            )
            self._log(f"Kaggle postprocess completed request_id={self.result.get('request_id')} zip={zip_path}")
            self._write_result()
            return self.result
        except Exception as exc:
            self.result.update(
                {
                    "status": "failed",
                    "failure_reason": self.result.get("failure_reason") or self._failure_reason_from_error(str(exc)),
                    "completed_at": utc_now_iso(),
                }
            )
            self.result.setdefault("errors", []).append(str(exc))
            self._write_result()
            self._log(f"Kaggle postprocess failed reason={self.result['failure_reason']} error={exc}")
            raise

    def _validate_config(self) -> None:
        if not self.settings.kaggle_kernel_ref:
            raise RuntimeError("KAGGLE_KERNEL_REF is not configured.")
        if not self.settings.kaggle_dataset_id:
            raise RuntimeError("KAGGLE_DATASET_ID is not configured.")
        if not self.bundle_path.exists():
            raise FileNotFoundError(f"Bundle path not found for Kaggle: {self.bundle_path}")
        if not (self.bundle_path / "Chunk").exists():
            raise FileNotFoundError(f"Bundle Chunk/ folder not found for Kaggle: {self.bundle_path / 'Chunk'}")
        if not self.kernel_template_dir.exists():
            raise FileNotFoundError(f"Kaggle kernel template not found: {self.kernel_template_dir}")
        kaggle_json = Path.home() / ".kaggle" / "kaggle.json"
        if not ((self.settings.kaggle_username and self.settings.kaggle_key) or kaggle_json.exists()):
            raise RuntimeError("Kaggle credentials missing. Set KAGGLE_USERNAME/KAGGLE_KEY or configure ~/.kaggle/kaggle.json.")
        self._log(
            "Kaggle config ok "
            f"kernel_ref={self.settings.kaggle_kernel_ref} "
            f"dataset_id={self.settings.kaggle_dataset_id} "
            f"max_attempts={self.settings.kaggle_max_attempts}"
        )

    def _run_cmd(
        self,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        timeout: int | None = None,
        stream: bool = False,
    ) -> str:
        safe_cmd = " ".join(str(part) for part in cmd)
        self._log(f">>> {safe_cmd}")
        env = os.environ.copy()
        if self.settings.kaggle_username:
            env["KAGGLE_USERNAME"] = self.settings.kaggle_username
        if self.settings.kaggle_key:
            env["KAGGLE_KEY"] = self.settings.kaggle_key
        if stream:
            proc = subprocess.Popen(
                list(map(str, cmd)),
                cwd=str(cwd) if cwd else None,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            lines: list[str] = []
            for raw_line in proc.stdout:
                line = raw_line.rstrip()
                if line:
                    lines.append(line)
                    self._log(line)
            returncode = proc.wait(timeout=timeout)
            if returncode != 0:
                raise subprocess.CalledProcessError(returncode, cmd, output="\n".join(lines))
            return "\n".join(lines)
        completed = subprocess.run(
            list(map(str, cmd)),
            cwd=str(cwd) if cwd else None,
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        output = completed.stdout or ""
        if output.strip():
            self._log(output.strip())
        return output

    def _ensure_kaggle_cli(self) -> None:
        out = self._run_cmd(["kaggle", "--version"], timeout=30)
        self._log(f"kaggle cli available: {out.strip()}")

    def _build_pack(self) -> None:
        self._log(f"dataset pack build started book_stem={self.book_stem}")
        if self.pack_dir.exists():
            shutil.rmtree(self.pack_dir)
        ensure_dir(self.pack_dir / "sgk_extract")
        ensure_dir(self.pack_dir / "Output")
        write_json(
            self.pack_dir / "run_request.json",
            {
                "expected_book_stem": self.book_stem,
                "requested_at": utc_now_iso(),
            },
        )
        (self.pack_dir / "book_stem.txt").write_text(self.book_stem, encoding="utf-8")
        read_back = (self.pack_dir / "book_stem.txt").read_text(encoding="utf-8").strip()
        if read_back != self.book_stem:
            raise RuntimeError(f"book_stem.txt verification failed: expected {self.book_stem!r}, got {read_back!r}")

        src_code = project_root() / "app" / "pipeline" / "chunk_postprocess.py"
        if not src_code.exists():
            raise FileNotFoundError(f"Missing chunk_postprocess.py: {src_code}")
        shutil.copy2(src_code, self.pack_dir / "sgk_extract" / "chunk_postprocess.py")

        dst_book = self.pack_dir / "Output" / self.book_stem
        shutil.copytree(self.bundle_path, dst_book)
        output_books = sorted(p.name for p in (self.pack_dir / "Output").iterdir() if p.is_dir())
        if output_books != [self.book_stem]:
            raise RuntimeError(f"Kaggle pack Output integrity failed: expected {[self.book_stem]}, got {output_books}")

        dataset_slug = self.settings.kaggle_dataset_id.split("/", 1)[1] if "/" in self.settings.kaggle_dataset_id else self.settings.kaggle_dataset_id
        write_json(
            self.pack_dir / "dataset-metadata.json",
            {
                "title": dataset_slug,
                "id": self.settings.kaggle_dataset_id,
                "licenses": [{"name": "CC0-1.0"}],
            },
        )
        self._log(f"dataset pack build completed pack_dir={self.pack_dir}")

    def _push_dataset(self) -> None:
        self._log("dataset push started")
        self._run_cmd(
            [
                "kaggle",
                "datasets",
                "version",
                "-p",
                str(self.pack_dir),
                "-m",
                f"auto upload: {self.book_stem}",
                "--dir-mode",
                "zip",
            ],
            timeout=1800,
            stream=True,
        )
        self._log("dataset push completed")

    def _wait_for_dataset_marker_ready(self) -> None:
        timeout_sec = 90
        poll_sec = 5
        started = time.monotonic()
        self._log("dataset marker propagation wait started")
        with tempfile.TemporaryDirectory(prefix="kaggle_dataset_marker_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            while time.monotonic() - started < timeout_sec:
                for old_item in tmp_path.iterdir():
                    if old_item.is_dir():
                        shutil.rmtree(old_item)
                    else:
                        old_item.unlink()
                try:
                    self._run_cmd(
                        [
                            "kaggle",
                            "datasets",
                            "download",
                            self.settings.kaggle_dataset_id,
                            "-f",
                            "book_stem.txt",
                            "-p",
                            str(tmp_path),
                            "-o",
                            "-q",
                        ],
                        timeout=120,
                    )
                    remote_marker = self._read_downloaded_marker(tmp_path, "book_stem.txt")
                    self._log(f"dataset marker remote={remote_marker!r} expected={self.book_stem!r}")
                    if remote_marker == self.book_stem:
                        self._log("dataset marker propagation ready")
                        return
                except Exception as exc:
                    self._log(f"dataset marker polling warning: {exc}")
                time.sleep(poll_sec)
        self._log("dataset marker propagation not confirmed; kernel stale retry guard will handle mismatch")

    @staticmethod
    def _read_downloaded_marker(download_dir: Path, marker_name: str) -> str:
        marker_path = download_dir / marker_name
        if marker_path.exists():
            return marker_path.read_text(encoding="utf-8").strip()
        for zip_path in sorted(download_dir.glob("*.zip")):
            try:
                with zipfile.ZipFile(zip_path, "r") as archive:
                    if marker_name in archive.namelist():
                        with archive.open(marker_name) as fh:
                            return fh.read().decode("utf-8").strip()
            except Exception:
                continue
        raise FileNotFoundError(f"Marker {marker_name!r} not found in {download_dir}")

    def _run_kernel_with_retries(self) -> Path:
        max_attempts = max(1, int(self.settings.kaggle_max_attempts))
        stale_retry_delay = 40
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            self.result["attempts"] = attempt
            request_id = uuid.uuid4().hex[:8]
            self.result["request_id"] = request_id
            request = {
                "expected_book_stem": self.book_stem,
                "request_id": request_id,
                "requested_at": utc_now_iso(),
                "attempt": attempt,
            }
            self._prepare_kernel_work_dir(request)
            expected_zip = self.dl_dir / f"{self.book_stem}_{request_id}_postprocessed.zip"
            status_specific = self.dl_dir / f"current_run_status_{request_id}.json"
            status_generic = self.dl_dir / "current_run_status.json"
            self._log(f"kernel attempt {attempt}/{max_attempts} request_id={request_id} expected_zip={expected_zip.name}")

            try:
                self._push_kernel()
                self._clean_dl_dir()
                self._download_kernel_output()
                if expected_zip.exists():
                    self._log(f"request-specific zip found: {expected_zip}")
                    return expected_zip

                status_info, status_file_used = self._read_status_info(status_specific, status_generic)
                status_request_id = status_info.get("request_id", "") if status_info else ""
                request_id_matches = bool(status_request_id) and status_request_id == request_id
                failure_reason = status_info.get("failure_reason", "") if request_id_matches else ""
                is_stale_dataset = failure_reason == "stale_dataset_mismatch"
                is_stale_artifact = bool(status_info) and not request_id_matches
                if is_stale_dataset:
                    self._log(f"stale_dataset_mismatch attempt={attempt} status={status_info}")
                    self.result["failure_reason"] = "stale_dataset_mismatch"
                elif is_stale_artifact:
                    self._log(
                        f"stale artifact attempt={attempt} status_file={status_file_used} "
                        f"status_request_id={status_request_id} current_request_id={request_id}"
                    )
                    self.result["failure_reason"] = "stale_artifact"

                if (is_stale_dataset or is_stale_artifact) and attempt < max_attempts:
                    time.sleep(stale_retry_delay)
                    continue

                found_zips = sorted(p.name for p in self.dl_dir.glob("*_postprocessed.zip"))
                raise FileNotFoundError(
                    f"Missing request-specific Kaggle zip {expected_zip.name}; "
                    f"found_zips={found_zips}; status_file={status_file_used}; status={status_info}"
                )
            except Exception as exc:
                last_error = exc
                self.result.setdefault("errors", []).append(f"attempt {attempt}: {exc}")
                if attempt >= max_attempts:
                    break
                self._log(f"kernel attempt {attempt} failed; retrying: {exc}")
                time.sleep(stale_retry_delay)
        assert last_error is not None
        raise last_error

    def _prepare_kernel_work_dir(self, request: dict[str, Any]) -> None:
        if self.kernel_work_dir.exists():
            shutil.rmtree(self.kernel_work_dir)
        shutil.copytree(self.kernel_template_dir, self.kernel_work_dir)
        self._write_kernel_metadata()
        script_path = self.kernel_work_dir / "script.py"
        script_text = script_path.read_text(encoding="utf-8")
        embedded_line = f"_EMBEDDED_RUN_REQUEST_JSON = {json.dumps(json.dumps(request, ensure_ascii=False))}"
        updated_text, replaced = _EMBEDDED_REQUEST_PATTERN.subn(embedded_line, script_text, count=1)
        if replaced != 1:
            raise RuntimeError(f"Embedded run request placeholder not found in kernel script: {script_path}")
        script_path.write_text(updated_text, encoding="utf-8")
        write_json(self.kernel_work_dir / "run_request.json", request)

    def _write_kernel_metadata(self) -> None:
        kernel_slug_value = self.settings.kaggle_kernel_ref.split("/", 1)[1] if "/" in self.settings.kaggle_kernel_ref else self.settings.kaggle_kernel_ref
        write_json(
            self.kernel_work_dir / "kernel-metadata.json",
            {
                "id": self.settings.kaggle_kernel_ref,
                "title": kernel_slug_value,
                "code_file": "script.py",
                "language": "python",
                "kernel_type": "script",
                "is_private": True,
                "enable_gpu": False,
                "enable_internet": True,
                "dataset_sources": [self.settings.kaggle_dataset_id],
                "competition_sources": [],
            },
        )

    def _push_kernel(self) -> None:
        self._log("kernel push started")
        self._run_cmd(["kaggle", "kernels", "push", "-p", str(self.kernel_work_dir)], timeout=300, stream=True)
        self._log("kernel push submitted; waiting for completion")
        while True:
            status_text = self._run_cmd(["kaggle", "kernels", "status", self.settings.kaggle_kernel_ref], timeout=60)
            self._log(f"kernel status: {status_text.strip()}")
            if "KernelWorkerStatus.COMPLETE" in status_text:
                self._log("kernel completed")
                return
            if "KernelWorkerStatus.FAILED" in status_text or "KernelWorkerStatus.ERROR" in status_text:
                self.result["failure_reason"] = "kernel_failed"
                raise RuntimeError(f"Kaggle kernel failed: {status_text.strip()}")
            time.sleep(max(1, int(self.settings.kaggle_poll_seconds)))

    def _clean_dl_dir(self) -> None:
        ensure_dir(self.dl_dir)
        removed: list[str] = []
        for entry in sorted(self.dl_dir.iterdir()):
            if entry.is_file() and entry.name.endswith("_postprocessed.zip"):
                entry.unlink()
                removed.append(entry.name)
            elif entry.is_file() and entry.name.startswith("current_run_status"):
                entry.unlink()
                removed.append(entry.name)
            elif entry.is_dir() and entry.name != self.book_stem:
                shutil.rmtree(entry)
                removed.append(entry.name + "/")
        self._log(f"download dir cleaned removed={removed}")

    def _download_kernel_output(self) -> None:
        self._log(f"kernel output download started dl_dir={self.dl_dir}")
        ensure_dir(self.dl_dir)
        self._run_cmd(
            ["kaggle", "kernels", "output", self.settings.kaggle_kernel_ref, "-p", str(self.dl_dir), "--force"],
            timeout=900,
            stream=True,
        )
        self._log("kernel output download completed")

    def _read_status_info(self, status_specific: Path, status_generic: Path) -> tuple[dict[str, Any], str]:
        for path in [status_specific, status_generic]:
            if path.exists():
                try:
                    data = read_json(path)
                    return data if isinstance(data, dict) else {}, path.name
                except Exception as exc:
                    self._log(f"failed to parse status file {path.name}: {exc}")
                    return {}, path.name
        return {}, "none"

    def _validate_zip_top_level(self, zip_path: Path) -> None:
        if not zip_path.exists():
            raise FileNotFoundError(f"Missing Kaggle output zip: {zip_path}")
        with zipfile.ZipFile(zip_path, "r") as archive:
            top_levels = sorted({name.split("/", 1)[0] for name in archive.namelist() if name and not name.endswith("/")})
        if len(top_levels) != 1 or top_levels[0] != self.book_stem:
            self.result["failure_reason"] = "zip_stem_mismatch"
            raise RuntimeError(
                f"Kaggle zip top-level mismatch. expected={self.book_stem!r} got={top_levels} zip={zip_path}"
            )
        self._log(f"zip validation passed zip={zip_path} top_level={top_levels[0]}")

    def _safe_extract_zip_to_output(self, zip_path: Path) -> Path:
        output_base = output_root()
        dst = output_base / self.book_stem
        with zipfile.ZipFile(zip_path, "r") as archive:
            members = archive.infolist()
            for member in members:
                member_path = Path(member.filename)
                if member_path.is_absolute() or ".." in member_path.parts:
                    self.result["failure_reason"] = "unsafe_zip_path"
                    raise RuntimeError(f"Unsafe path in Kaggle zip: {member.filename}")
            if dst.exists():
                shutil.rmtree(dst)
            ensure_dir(output_base)
            archive.extractall(output_base)
        if not dst.exists():
            raise RuntimeError(f"Kaggle zip extracted but expected output folder is missing: {dst}")
        self._log(f"Kaggle zip applied to {dst}")
        return dst

    @staticmethod
    def _failure_reason_from_error(error: str) -> str:
        lowered = error.lower()
        if "stale_dataset_mismatch" in lowered:
            return "stale_dataset_mismatch"
        if "top-level mismatch" in lowered or "stem mismatch" in lowered:
            return "zip_stem_mismatch"
        if "kernel failed" in lowered:
            return "kernel_failed"
        if "missing request-specific kaggle zip" in lowered or "missing kaggle output zip" in lowered:
            return "expected_zip_missing"
        if "credentials" in lowered:
            return "credentials_missing"
        if "kaggle" in lowered and "not found" in lowered:
            return "kaggle_cli_missing"
        return "kaggle_failed"

    def _write_result(self) -> None:
        write_json(kaggle_result_path(self.job_id), self.result)

    def _log(self, message: str) -> None:
        line = f"{utc_now_iso()} {message}"
        append_job_log(self.kaggle_log_path, line)
        append_job_log(self.bundle_log_path, f"{utc_now_iso()} [kaggle] {message}")
        append_job_log(job_log_path(self.job_id), f"{utc_now_iso()} [kaggle] {message}")
