#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
ROTATION_STATE_PATH = PROJECT_ROOT / "workspace" / "gemini_rotation_state.json"
DEBUG_URL = "http://localhost:8100/api/debug/gemini-keys"


@dataclass
class EnvKeys:
    combined: list[str]
    numbered: list[tuple[int, str]]


def fetch_debug(url: str) -> dict:
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_env_keys(lines: list[str]) -> EnvKeys:
    combined: list[str] = []
    numbered: list[tuple[int, str]] = []
    numbered_pattern = re.compile(r"^GEMINI_API_KEY_(\d+)=(.*)$")
    for line in lines:
        if line.startswith("GEMINI_API_KEYS="):
            value = line.split("=", 1)[1].strip()
            combined = [part.strip() for part in value.split(",") if part.strip()]
            continue
        match = numbered_pattern.match(line)
        if match:
            numbered.append((int(match.group(1)), match.group(2).strip()))
    numbered.sort(key=lambda item: item[0])
    return EnvKeys(combined=combined, numbered=numbered)


def split_keep_keys(env_keys: EnvKeys, dead_indices: set[int]) -> tuple[list[str], list[str], str]:
    combined_len = len(env_keys.combined)
    numbered_values = [value for _, value in env_keys.numbered]
    combined_keep = [
        key for index, key in enumerate(env_keys.combined)
        if index not in dead_indices
    ]
    numbered_keep = [
        key for offset, key in enumerate(numbered_values)
        if (combined_len + offset) not in dead_indices
    ]

    seen: set[str] = set()
    deduped_combined: list[str] = []
    deduped_numbered: list[str] = []
    for key in combined_keep:
        if key not in seen:
            deduped_combined.append(key)
            seen.add(key)
    for key in numbered_keep:
        if key not in seen:
            deduped_numbered.append(key)
            seen.add(key)

    if env_keys.combined and env_keys.numbered:
        style = "both"
    elif env_keys.numbered:
        style = "GEMINI_API_KEY_N"
    elif env_keys.combined:
        style = "GEMINI_API_KEYS"
    else:
        style = "none"
    return deduped_combined, deduped_numbered, style


def rewrite_env_lines(lines: list[str], combined_keep: list[str], numbered_keep: list[str], had_combined: bool, had_numbered: bool) -> list[str]:
    new_lines: list[str] = []
    wrote_combined = False
    wrote_numbered = False
    numbered_pattern = re.compile(r"^GEMINI_API_KEY_\d+=")

    for line in lines:
        if line.startswith("GEMINI_API_KEYS="):
            if had_combined and not wrote_combined:
                new_lines.append(f"GEMINI_API_KEYS={','.join(combined_keep)}\n")
                wrote_combined = True
            continue
        if numbered_pattern.match(line):
            if had_numbered and not wrote_numbered:
                for index, key in enumerate(numbered_keep, start=1):
                    new_lines.append(f"GEMINI_API_KEY_{index}={key}\n")
                wrote_numbered = True
            continue
        new_lines.append(line)

    if had_combined and not wrote_combined:
        new_lines.append(f"GEMINI_API_KEYS={','.join(combined_keep)}\n")
    if had_numbered and not wrote_numbered:
        for index, key in enumerate(numbered_keep, start=1):
            new_lines.append(f"GEMINI_API_KEY_{index}={key}\n")
    return new_lines


def backup_file(path: Path, suffix: str) -> Path:
    backup = path.with_name(f"{path.name}.{suffix}")
    shutil.copy2(path, backup)
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune only debug-dead Gemini API keys from .env.")
    parser.add_argument("--dry-run", action="store_true", help="Print summary without editing files.")
    parser.add_argument("--url", default=DEBUG_URL, help="Gemini key debug endpoint URL.")
    args = parser.parse_args()

    try:
        debug = fetch_debug(args.url)
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read debug endpoint: {exc}", file=sys.stderr)
        return 2

    keys = debug.get("keys") or []
    dead_indices = {int(item["index"]) for item in keys if item.get("status") == "dead" and "index" in item}
    keep_indices = [int(item["index"]) for item in keys if item.get("status") != "dead" and "index" in item]

    if not ENV_PATH.exists():
        print(f"ERROR: missing {ENV_PATH}", file=sys.stderr)
        return 2

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    env_keys = parse_env_keys(lines)
    combined_keep, numbered_keep, style = split_keep_keys(env_keys, dead_indices)
    before_count = len(env_keys.combined) + len(env_keys.numbered)
    after_count = len(combined_keep) + len(numbered_keep)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    print("Gemini key prune summary")
    print(f"config_style={style}")
    if style == "both":
        print("warning=Both GEMINI_API_KEYS and GEMINI_API_KEY_N exist; pruning both and deduplicating remaining keys.")
    print(f"debug_total_keys={debug.get('key_count', len(keys))}")
    print(f"env_key_count_before={before_count}")
    print(f"dead_count={len(dead_indices)}")
    print(f"keep_count={len(keep_indices)}")
    print(f"env_key_count_after={after_count}")
    print(f"dead_indices={sorted(dead_indices)}")
    print(f"keep_indices={keep_indices}")

    if args.dry_run:
        print("dry_run=true")
        return 0

    env_backup = backup_file(ENV_PATH, f"backup-prune-gemini-keys-{timestamp}")
    new_lines = rewrite_env_lines(
        lines,
        combined_keep,
        numbered_keep,
        had_combined=bool(env_keys.combined),
        had_numbered=bool(env_keys.numbered),
    )
    ENV_PATH.write_text("".join(new_lines), encoding="utf-8")

    rotation_backup = None
    if ROTATION_STATE_PATH.exists():
        rotation_backup = backup_file(ROTATION_STATE_PATH, f"backup-prune-{timestamp}")
        ROTATION_STATE_PATH.unlink()

    print(f"env_backup={env_backup.name}")
    print(f"rotation_state_backup={rotation_backup.name if rotation_backup else '-'}")
    print("rotation_state_reset=true")
    print("No key values were printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
