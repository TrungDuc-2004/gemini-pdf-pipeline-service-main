#scripst/kaggle/kernels/debug-cutlines-auto/script.py
import json, os, sys, zipfile, shutil, subprocess
from pathlib import Path

# --- ENV ---
os.environ["DISABLE_MODEL_SOURCE_CHECK"] = "True"
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
os.environ["DISABLE_PDF_UPDATE"] = "0"   # ✅ cho phép fitz update PDF

# Rewritten by cli.py before each `kaggle kernels push`.
_EMBEDDED_RUN_REQUEST_JSON = "{\"expected_book_stem\": \"SGK-KHMT-11_3c55de53\", \"request_id\": \"a1535ad9\", \"requested_at\": \"2026-04-18T01:50:06.893406+00:00\", \"attempt\": 1}"

def sh(cmd):
    print(">>>", cmd)
    subprocess.run(cmd, shell=True, check=True)

# ==============
# (A) Load run_request.json — written by local CLI before every kernel push
# ==============
run_request: dict = {}
_request_file_used: str = "not found"

try:
    _embedded_request = json.loads(_EMBEDDED_RUN_REQUEST_JSON)
    if isinstance(_embedded_request, dict) and _embedded_request:
        run_request = _embedded_request
        _request_file_used = "embedded:script.py"
except Exception as _embedded_err:
    print(f"[REQUEST] Failed to parse embedded payload: {_embedded_err}")

if not run_request:
    _REQUEST_FILE_CANDIDATES = [
        Path(__file__).parent / "run_request.json",
        Path("/kaggle/working/run_request.json"),
    ]
    for _rfc in _REQUEST_FILE_CANDIDATES:
        if _rfc.exists():
            try:
                run_request = json.loads(_rfc.read_text(encoding="utf-8"))
                _request_file_used = str(_rfc)
                break
            except Exception as _rfe:
                print(f"[REQUEST] Failed to parse {_rfc}: {_rfe}")

request_id = run_request.get("request_id", "unknown")
expected_book_stem_from_request = run_request.get("expected_book_stem", "").strip()
print(f"[REQUEST] request_file={_request_file_used}")
print(f"[REQUEST] request_id={request_id!r}")
print(f"[REQUEST] expected_book_stem={expected_book_stem_from_request!r}")
print(f"[REQUEST] attempt={run_request.get('attempt', 'N/A')}")
print(f"[REQUEST] requested_at={run_request.get('requested_at', 'N/A')}")

# ==============
# (B) Run status sentinels — written to /kaggle/working/ so they get downloaded
# ==============
# Generic alias kept for debug / backward-compat
STATUS_FILE = Path("/kaggle/working/current_run_status.json")
# Request-specific file — local CLI uses this as the authoritative source of truth
STATUS_FILE_SPECIFIC = Path("/kaggle/working") / f"current_run_status_{request_id}.json"

def write_status(status: str, *, failure_reason: str = "", **extra) -> None:
    data: dict = {
        "request_id": request_id,
        "expected_book_stem": expected_book_stem_from_request,
        "status": status,
    }
    if failure_reason:
        data["failure_reason"] = failure_reason
    data.update(extra)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    # Request-specific file first (local CLI validates against this)
    try:
        STATUS_FILE_SPECIFIC.write_text(payload, encoding="utf-8")
    except Exception as _se:
        print(f"[STATUS] Failed to write specific status file ({STATUS_FILE_SPECIFIC.name}): {_se}")
    # Generic alias for debug / backward-compat
    try:
        STATUS_FILE.write_text(payload, encoding="utf-8")
    except Exception as _se:
        print(f"[STATUS] Failed to write generic status file: {_se}")

def _write_unhandled_exception_status(exc_type, exc, tb) -> None:
    import traceback

    try:
        write_status(
            "failed",
            failure_reason="unhandled_exception",
            message=str(exc),
            stage=globals().get("CURRENT_STAGE", "unknown"),
            traceback="".join(traceback.format_exception(exc_type, exc, tb))[-4000:],
        )
    except Exception as _status_exc:
        print(f"[STATUS] Failed to write unhandled exception status: {_status_exc}")
    sys.__excepthook__(exc_type, exc, tb)

sys.excepthook = _write_unhandled_exception_status

write_status("started")

# ==============
# (1) Install deps
# ==============
CURRENT_STAGE = "install_dependencies"
sh("python -m pip -q install --upgrade pip")

# fitz + pdf render
sh("python -m pip -q install PyMuPDF==1.27.1 pypdfium2")

# paddle + paddleocr 2.x minimal
sh("python -m pip -q install paddlepaddle==3.3.0")
sh("python -m pip -q uninstall -y paddleocr paddlex || true")
sh("python -m pip -q install --no-deps paddleocr==2.7.3")
sh("python -m pip -q install pyclipper shapely imgaug pillow tqdm lmdb attrdict fire rapidfuzz visualdl")

# --- NumPy patch cho imgaug (np.sctypes removed in NumPy 2.x) ---
import numpy as np
if not hasattr(np, "sctypes"):
    np.sctypes = {
        "int":     [np.int8, np.int16, np.int32, np.int64],
        "uint":    [np.uint8, np.uint16, np.uint32, np.uint64],
        "float":   [np.float16, np.float32, np.float64],
        "complex": [np.complex64, np.complex128],
        "others":  [np.bool_, np.bytes_, np.str_, np.void],
    }

# ==============
# (2) Find dataset root — prefer exact owner/slug match, then score fallback
# ==============
CURRENT_STAGE = "resolve_dataset_root"
INPUT_ROOT = Path("/kaggle/input")
print("INPUT_ROOT entries:", [p.name for p in INPUT_ROOT.iterdir()])

datasets_root = INPUT_ROOT / "datasets"
print("datasets_root exists?", datasets_root.exists())

# Print full input tree for diagnostics
subprocess.run("find /kaggle/input -maxdepth 6 -type f | head -n 200", shell=True)

def resolve_dataset_root(prefer_owner="dat261303", prefer_slug="kaggle-pack"):
    """
    Return (ds_root, ds_base, mode)
      - ds_root: /kaggle/input/datasets/<owner>/<slug>
      - ds_base: ds_root or ds_root/kaggle_pack (depending on upload mode)
      - mode: "folder-mode" or "zip-mode"

    Selection strategy:
      1. Exact owner+slug match (prefer_owner / prefer_slug) — if found, use it directly
      2. Fallback: score all candidates, pick highest
      In both cases print full candidate list for diagnostics.
    """
    if not datasets_root.exists():
        raise FileNotFoundError(f"Missing: {datasets_root}")

    candidates = []
    exact_match = None

    for owner_dir in sorted(datasets_root.iterdir()):
        if not owner_dir.is_dir():
            continue
        for ds_dir in sorted(owner_dir.iterdir()):
            if not ds_dir.is_dir():
                continue

            base = ds_dir / "kaggle_pack" if (ds_dir / "kaggle_pack").is_dir() else ds_dir

            has_output_dir = (base / "Output").is_dir()
            has_output_zip = (base / "Output.zip").is_file()
            has_code_dir   = (base / "sgk_extract").is_dir()
            has_code_zip   = (base / "sgk_extract.zip").is_file()
            has_marker     = (base / "book_stem.txt").is_file()

            if not (has_output_dir or has_output_zip or has_code_dir or has_code_zip):
                continue

            score = 0
            score += 10 if has_output_dir else 0
            score += 10 if has_output_zip else 0
            score += 5  if has_code_dir   else 0
            score += 5  if has_code_zip   else 0
            score += 3  if has_marker     else 0
            if owner_dir.name == prefer_owner:
                score += 3
            if prefer_slug and (prefer_slug.lower() in ds_dir.name.lower()):
                score += 3

            is_exact = (owner_dir.name == prefer_owner and prefer_slug.lower() in ds_dir.name.lower())

            entry = {
                "score": score,
                "ds_root": ds_dir,
                "ds_base": base,
                "has_output_dir": has_output_dir,
                "has_output_zip": has_output_zip,
                "has_code_dir": has_code_dir,
                "has_code_zip": has_code_zip,
                "has_marker": has_marker,
                "is_exact": is_exact,
            }
            candidates.append(entry)

            if is_exact:
                if exact_match is None or score > exact_match["score"]:
                    exact_match = entry

    print(f"\n[DATASET SELECTION] Found {len(candidates)} candidate(s):")
    for c in sorted(candidates, key=lambda x: -x["score"]):
        print(
            f"  {'*** EXACT ' if c['is_exact'] else '    '}score={c['score']:3d}"
            f"  {c['ds_root']}"
            f"  base={c['ds_base']}"
            f"  out_dir={c['has_output_dir']}  out_zip={c['has_output_zip']}"
            f"  code_dir={c['has_code_dir']}  code_zip={c['has_code_zip']}"
            f"  marker={c['has_marker']}"
        )

    if not candidates:
        raise FileNotFoundError(
            "Cannot find dataset under /kaggle/input/datasets that contains "
            "Output/Output.zip/sgk_extract.\n"
            f"Scanned: {[str(p) for p in sorted(datasets_root.glob('*/*'))[:30]]}"
        )

    if exact_match is not None:
        chosen = exact_match
        print(f"\n[DATASET SELECTION] Using EXACT match: {chosen['ds_root']}")
    else:
        raise RuntimeError(
            f"[DATASET SELECTION] FATAL: no dataset found matching "
            f"owner={prefer_owner!r} slug={prefer_slug!r}.\n"
            f"Refusing to use a fallback dataset — this would silently process the wrong book.\n"
            f"  datasets_root={datasets_root}\n"
            f"  candidates scanned: {[str(c['ds_root']) for c in candidates]}\n"
            f"Action: ensure the dataset '{prefer_owner}/{prefer_slug}' exists and is attached to the kernel."
        )

    ds_root = chosen["ds_root"]
    ds_base = chosen["ds_base"]
    has_output_dir = chosen["has_output_dir"]
    has_output_zip = chosen["has_output_zip"]

    if has_output_dir:
        mode = "folder-mode"
    elif has_output_zip:
        mode = "zip-mode"
    else:
        mode = "folder-mode"

    print(f"[DATASET SELECTION] ds_root={ds_root}")
    print(f"[DATASET SELECTION] ds_base={ds_base}")
    print(f"[DATASET SELECTION] mode={mode}")
    print(f"[DATASET SELECTION] flags: has_output_dir={has_output_dir} has_output_zip={has_output_zip}"
          f" has_code_dir={chosen['has_code_dir']} has_code_zip={chosen['has_code_zip']}"
          f" has_marker={chosen['has_marker']}")

    return ds_root, ds_base, mode

ds_root, ds_base, mode = resolve_dataset_root()

# ==============
# (3) Copy/unzip into working (writeable)
# ==============
CURRENT_STAGE = "copy_dataset_to_working"
WORK = Path("/kaggle/working/kaggle_pack")
shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(parents=True, exist_ok=True)

def unzip(src_zip: Path, dst_dir: Path):
    print("Unzipping:", src_zip, "->", dst_dir)
    with zipfile.ZipFile(src_zip, "r") as z:
        z.extractall(dst_dir)

if mode == "folder-mode":
    shutil.copytree(ds_base, WORK, dirs_exist_ok=True)
else:
    out_zip = ds_base / "Output.zip"
    code_zip = ds_base / "sgk_extract.zip"
    assert out_zip.exists() and code_zip.exists(), \
        f"Expected Output.zip & sgk_extract.zip under {ds_base}"
    unzip(out_zip, WORK)
    unzip(code_zip, WORK)

# Copy marker book_stem.txt into WORK (zip-mode may miss it)
marker_src = ds_base / "book_stem.txt"
marker_dst = WORK / "book_stem.txt"

print(f"\n[MARKER] marker_src={marker_src}  exists={marker_src.exists()}")
if marker_src.exists():
    shutil.copy2(marker_src, marker_dst)
    print(f"[MARKER] Copied: {marker_src} -> {marker_dst}")
    print(f"[MARKER] marker_src content: {marker_src.read_text(encoding='utf-8').strip()!r}")
else:
    print(f"[MARKER] WARNING: book_stem.txt not found at: {marker_src}")
    print(f"[MARKER] ds_base contents: {sorted(p.name for p in ds_base.iterdir()) if ds_base.exists() else 'N/A'}")

print(f"[MARKER] marker_dst={marker_dst}  exists={marker_dst.exists()}")
if marker_dst.exists():
    print(f"[MARKER] marker_dst content: {marker_dst.read_text(encoding='utf-8').strip()!r}")

print("\n[WORK TREE]")
subprocess.run(f"find '{WORK}' -maxdepth 3 -type d | head -n 80", shell=True)

# Print Output subdirs for diagnostics
work_output = WORK / "Output"
output_subdirs = sorted(d.name for d in work_output.iterdir() if d.is_dir()) if work_output.exists() else []
print(f"[OUTPUT] WORK/Output exists={work_output.exists()}")
print(f"[OUTPUT] WORK/Output subdirs: {output_subdirs}")

# ==============
# (4) Resolve book_stem — marker is authoritative; no silent fallback
# ==============
CURRENT_STAGE = "resolve_book_stem"
sys.path.append(str(WORK / "sgk_extract"))
import importlib
import chunk_postprocess as cp
importlib.reload(cp)

print("cp loaded from:", cp.__file__)
cp.FORCE_REPROCESS = os.getenv("FORCE_REPROCESS", "0") == "1"
print("FORCE_REPROCESS =", cp.FORCE_REPROCESS)

book_stem = None
book_stem_source = None

# 1) Marker at WORK/book_stem.txt (written by build_kaggle_pack, copied above)
if marker_dst.exists():
    candidate = marker_dst.read_text(encoding="utf-8").strip()
    if candidate:
        book_stem = candidate
        book_stem_source = f"marker:{marker_dst}"
        print(f"[BOOK_STEM] Loaded from marker: {book_stem!r}")
    else:
        print(f"[BOOK_STEM] WARNING: marker_dst exists but is empty: {marker_dst}")
else:
    print(f"[BOOK_STEM] No marker at: {marker_dst}")

# 2) Env var BOOK_STEM (explicit override — only if marker absent or empty)
if not book_stem:
    env_bs = os.getenv("BOOK_STEM", "").strip()
    if env_bs:
        book_stem = env_bs
        book_stem_source = "env:BOOK_STEM"
        print(f"[BOOK_STEM] Loaded from BOOK_STEM env: {book_stem!r}")
    else:
        print(f"[BOOK_STEM] BOOK_STEM env not set.")

# 3) Auto-detect ONLY if Output contains exactly one book dir (no ambiguity)
if not book_stem:
    if len(output_subdirs) == 1:
        book_stem = output_subdirs[0]
        book_stem_source = f"auto-detect:single-dir"
        print(f"[BOOK_STEM] Auto-detected (single Output dir): {book_stem!r}")
    elif len(output_subdirs) > 1:
        # Multiple books — cannot auto-detect safely
        raise RuntimeError(
            f"[BOOK_STEM] FATAL: marker missing AND multiple books in WORK/Output — "
            f"cannot determine which book to process.\n"
            f"  ds_root={ds_root}\n"
            f"  ds_base={ds_base}\n"
            f"  mode={mode}\n"
            f"  marker_src={marker_src}  exists={marker_src.exists()}\n"
            f"  marker_dst={marker_dst}  exists={marker_dst.exists()}\n"
            f"  Output subdirs={output_subdirs}\n"
            f"Set BOOK_STEM env or ensure book_stem.txt is present in the dataset."
        )
    else:
        raise RuntimeError(
            f"[BOOK_STEM] FATAL: marker missing AND WORK/Output is empty or missing.\n"
            f"  ds_root={ds_root}\n"
            f"  ds_base={ds_base}\n"
            f"  mode={mode}\n"
            f"  marker_src={marker_src}  exists={marker_src.exists()}\n"
            f"  marker_dst={marker_dst}  exists={marker_dst.exists()}\n"
            f"  WORK/Output exists={work_output.exists()}\n"
            f"  Output subdirs={output_subdirs}"
        )

print(f"[BOOK_STEM] Final: {book_stem!r}  (source: {book_stem_source})")

write_status(
    "stem_resolved",
    resolved_book_stem=book_stem,
    book_stem_source=book_stem_source,
    marker_src=str(marker_src),
    marker_src_exists=marker_src.exists(),
    marker_src_content=(marker_src.read_text(encoding="utf-8").strip() if marker_src.exists() else None),
    marker_dst=str(marker_dst),
    marker_dst_exists=marker_dst.exists(),
    marker_dst_content=(marker_dst.read_text(encoding="utf-8").strip() if marker_dst.exists() else None),
    ds_root=str(ds_root),
    ds_base=str(ds_base),
    output_subdirs=output_subdirs,
)

# Primary check: run_request.json expected stem (written by local CLI before each push)
_expected_bs = expected_book_stem_from_request
# Secondary check: env var (belt-and-suspenders, but run_request.json takes priority)
if not _expected_bs:
    _expected_bs = os.getenv("EXPECTED_BOOK_STEM", "").strip()
    if _expected_bs:
        print(f"[BOOK_STEM] expected stem from EXPECTED_BOOK_STEM env (fallback): {_expected_bs!r}")

if _expected_bs:
    if book_stem != _expected_bs:
        _mismatch_detail = {
            "resolved_book_stem": book_stem,
            "book_stem_source": book_stem_source,
            "marker_src": str(marker_src),
            "marker_src_exists": marker_src.exists(),
            "marker_src_content": (marker_src.read_text(encoding="utf-8").strip() if marker_src.exists() else None),
            "marker_dst": str(marker_dst),
            "marker_dst_exists": marker_dst.exists(),
            "marker_dst_content": (marker_dst.read_text(encoding="utf-8").strip() if marker_dst.exists() else None),
            "ds_root": str(ds_root),
            "ds_base": str(ds_base),
            "output_subdirs": output_subdirs,
        }
        write_status("failed", failure_reason="stale_dataset_mismatch", **_mismatch_detail)
        raise RuntimeError(
            f"[BOOK_STEM] FATAL: resolved book_stem={book_stem!r} "
            f"does not match expected={_expected_bs!r}.\n"
            f"The kernel is running against a stale or wrong dataset version.\n"
            f"  request_id={request_id!r}  request_file={_request_file_used}\n"
            f"  marker_src={marker_src}  exists={marker_src.exists()}\n"
            f"  marker_src content={(marker_src.read_text(encoding='utf-8').strip() if marker_src.exists() else 'N/A')!r}\n"
            f"  marker_dst={marker_dst}  exists={marker_dst.exists()}\n"
            f"  marker_dst content={(marker_dst.read_text(encoding='utf-8').strip() if marker_dst.exists() else 'N/A')!r}\n"
            f"  ds_root={ds_root}\n"
            f"  ds_base={ds_base}\n"
            f"  WORK/Output subdirs={output_subdirs}\n"
            f"Action: re-upload the dataset with the correct book_stem.txt and wait for propagation."
        )
    print(f"[BOOK_STEM] expected={_expected_bs!r} matches resolved stem — OK")
else:
    print(f"[BOOK_STEM] No expected stem available (run_request.json missing or empty, env not set) — skipping cross-check")

# ==============
# (5) Validate: marker content must match an actual Output subdir
# ==============
CURRENT_STAGE = "validate_book_bundle"
book_dir = WORK / "Output" / book_stem

if not book_dir.exists():
    write_status(
        "failed",
        failure_reason="book_dir_missing",
        resolved_book_stem=book_stem,
        book_stem_source=book_stem_source,
        marker_dst_content=(marker_dst.read_text(encoding="utf-8").strip() if marker_dst.exists() else None),
        output_subdirs=output_subdirs,
        ds_root=str(ds_root),
        ds_base=str(ds_base),
    )
    raise RuntimeError(
        f"[VALIDATION] FATAL: WORK/Output/{book_stem} does not exist!\n"
        f"  book_stem={book_stem!r}  (from {book_stem_source})\n"
        f"  ds_root={ds_root}\n"
        f"  ds_base={ds_base}\n"
        f"  mode={mode}\n"
        f"  marker_src={marker_src}  exists={marker_src.exists()}\n"
        f"  marker_src content={(marker_src.read_text(encoding='utf-8').strip() if marker_src.exists() else 'N/A')!r}\n"
        f"  marker_dst={marker_dst}  exists={marker_dst.exists()}\n"
        f"  marker_dst content={(marker_dst.read_text(encoding='utf-8').strip() if marker_dst.exists() else 'N/A')!r}\n"
        f"  WORK/Output subdirs={output_subdirs}\n"
        f"The dataset was uploaded with book_stem={book_stem!r} but WORK/Output contains: {output_subdirs}.\n"
        f"This means the dataset was not rebuilt before uploading, or the wrong dataset version was selected."
    )

print(f"[VALIDATION] OK: WORK/Output/{book_stem} exists.")

# Remove stale other books from WORK/Output — only the current book should remain
stale_dirs = [d for d in output_subdirs if d != book_stem]
if stale_dirs:
    print(
        f"[CLEAN] Removing stale working output for other book(s): {stale_dirs}"
    )
    for _stale in stale_dirs:
        _stale_path = work_output / _stale
        if _stale_path.exists():
            shutil.rmtree(_stale_path)
            print(f"[CLEAN] Removed: {_stale_path}")
    print(f"[CLEAN] Working output now contains only {book_stem!r}")

chunk_root = book_dir / "Chunk"
if not chunk_root.exists():
    write_status(
        "failed",
        failure_reason="chunk_root_missing",
        resolved_book_stem=book_stem,
        chunk_root=str(chunk_root),
        book_dir_contents=sorted(p.name for p in book_dir.iterdir()) if book_dir.exists() else [],
    )
    raise RuntimeError(
        f"[VALIDATION] FATAL: Missing chunk_root: {chunk_root}\n"
        f"  book_dir={book_dir}\n"
        f"  book_dir contents: {sorted(p.name for p in book_dir.iterdir()) if book_dir.exists() else 'N/A'}"
    )

print(f"[VALIDATION] chunk_root={chunk_root}")
write_status(
    "processing",
    resolved_book_stem=book_stem,
    chunk_root=str(chunk_root),
)

# Lấy tất cả meta json (trừ keywords)
json_files = sorted([
    p for p in chunk_root.rglob("*.json")
    if (not p.name.endswith(".keywords.json"))
    and ("DebugCutlines" not in p.parts)         # bỏ debug folder
    and (not p.stem.endswith("_cutline"))        # bỏ *_cutline.json
])
print("ChunkRoot:", chunk_root)
print("Total meta json:", len(json_files))

ocr = cp.build_ocr()

ok = skip = fail = 0
last_debug_dir = None

CURRENT_STAGE = "postprocess_chunks"
for jp in json_files:
    try:
        meta = cp.read_json(jp)
    except Exception:
        print("[FAIL] JSON parse:", jp)
        fail += 1
        continue

    heading = str(meta.get("heading", "")).strip()
    heading_num = cp.extract_heading_num(heading)

    is_content_head = bool(meta.get("content_head", False))
    is_force_heading = (heading_num in getattr(cp, "FORCE_HEADING_NUMS", set()))

    if (not is_content_head) and (not is_force_heading):
        skip += 1
        continue

    pdf_path = jp.with_suffix(".pdf")
    if not pdf_path.exists():
        print("[FAIL] Missing chunk pdf:", pdf_path)
        fail += 1
        continue

    # skip nếu đã làm rồi (giữ logic cũ)
    already_done = (is_content_head and bool(meta.get(getattr(cp, "EXTRACT_KEY", "extract"), False))) or (
        (not is_content_head) and bool(meta.get(getattr(cp, "EXTRACT_HEADING_KEY", "extract_heading"), False))
    )
    if (not getattr(cp, "FORCE_REPROCESS", False)) and already_done:
        skip += 1
        continue

    try:
        out_dir = jp.parent / "DebugCutlines"
        shutil.rmtree(out_dir, ignore_errors=True)   # xoá debug cũ của chunk này
        out_dir.mkdir(parents=True, exist_ok=True)
        last_debug_dir = out_dir

        payload = cp.process_one_chunk(ocr, jp, pdf_path, out_dir)
        if payload is None:
            skip += 1
            continue

        ok += 1

        mark_extract = is_content_head
        mark_extract_heading = (not is_content_head) and (heading_num in getattr(cp, "FORCE_HEADING_NUMS", set()))
        cp.mark_chunk_processed(jp, meta, mark_extract=mark_extract, mark_extract_heading=mark_extract_heading)

    except Exception as e:
        print("[FAIL]", jp, "=>", repr(e))
        fail += 1

print("")
print("=== POSTPROCESS SUMMARY ===")
print("OK  :", ok)
print("SKIP:", skip)
print("FAIL:", fail)
print("debug_example:", str(last_debug_dir) if last_debug_dir else None)

# ==============
# (6) Zip result for download
# ==============
CURRENT_STAGE = "zip_result"
out_zip = Path("/kaggle/working") / f"{book_stem}_{request_id}_postprocessed.zip"
print(f"\n[ZIP] book_stem={book_stem!r}")
print(f"[ZIP] request_id={request_id!r}")
print(f"[ZIP] source dir : {WORK / 'Output' / book_stem}")
print(f"[ZIP] output zip : {out_zip}")
sh(f"cd '{WORK / 'Output'}' && zip -qr '{out_zip}' '{book_stem}'")

# Validate: zip must exist and top-level folder must match book_stem
if not out_zip.exists():
    write_status(
        "failed",
        failure_reason="zip_creation_failed",
        resolved_book_stem=book_stem,
        expected_zip=str(out_zip),
        source_dir_exists=(WORK / "Output" / book_stem).exists(),
    )
    raise RuntimeError(
        f"[ZIP VALIDATION] FATAL: zip was not created: {out_zip}\n"
        f"  book_stem={book_stem!r}\n"
        f"  source dir exists: {(WORK / 'Output' / book_stem).exists()}"
    )
with zipfile.ZipFile(out_zip, "r") as _vz:
    _vz_tops = sorted({p.split("/", 1)[0] for p in _vz.namelist() if p and not p.endswith("/")})
if len(_vz_tops) != 1 or _vz_tops[0] != book_stem:
    write_status(
        "failed",
        failure_reason="zip_content_mismatch",
        resolved_book_stem=book_stem,
        expected_top_level=book_stem,
        zip_top_levels=_vz_tops,
        zip_path=str(out_zip),
    )
    raise RuntimeError(
        f"[ZIP VALIDATION] FATAL: zip top-level folder mismatch.\n"
        f"  zip path             : {out_zip}\n"
        f"  expected top-level   : {book_stem!r}\n"
        f"  zip actually contains: {_vz_tops}\n"
        f"This indicates the working directory had stale content from another book_stem."
    )
print(f"[ZIP VALIDATION] OK: {out_zip.name} — top-level folder={book_stem!r}")
write_status(
    "completed",
    resolved_book_stem=book_stem,
    final_zip=str(out_zip),
    final_zip_name=out_zip.name,
    summary={"ok": ok, "skip": skip, "fail": fail},
)
print(f"\nDONE. Download this file from kernel Output: {out_zip}")
