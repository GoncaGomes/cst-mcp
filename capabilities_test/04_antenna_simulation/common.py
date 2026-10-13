"""Client-only evidence and independent numerical checks. No CST operations."""
from __future__ import annotations

import cmath
import hashlib
import json
import math
import os
import re
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Only stateless helpers are reused. Neither battery runner is constructed.
sys.path.insert(0, str(ROOT / "capabilities_test/01_project_geometry_materials"))
from preserving_stdio import preserve_cst_processes
from run_batch import (
    DEFAULT_CST_PATH, EXPECTED_UNITS, FIXED_VBA, SHAPES_QUERY, UNITS_BLOCK,
    UNITS_QUERY, exception_leaves, indeterminate, interpret, parse_shapes,
    parse_units, serialize, timestamp, walk_dicts,
)
sys.path.insert(0, str(ROOT / "capabilities_test/02_parameters"))
from run_parameter_brick import ensure_closed


class Stop(RuntimeError):
    """Demonstrated failure with known native state."""


class Incomplete(RuntimeError):
    """Insufficient evidence, including unknown native state."""


def safe_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return {"nonfinite": str(value)}
    if isinstance(value, complex):
        return {"real": safe_json(value.real), "imag": safe_json(value.imag)}
    if isinstance(value, dict):
        return {str(k): safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_json(v) for v in value]
    return serialize(value)


def atomic_text(path, text):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def write_json(path, value):
    atomic_text(path, json.dumps(safe_json(value), indent=2, allow_nan=False) + "\n")


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def executable_version(path):
    """Read Windows version resources without executing or loading the application."""
    if os.name != "nt" or not path.is_file():
        return None
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("version", use_last_error=True)
    api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    api.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    api.GetFileVersionInfoW.restype = wintypes.BOOL
    api.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    api.VerQueryValueW.restype = wintypes.BOOL
    ignored = wintypes.DWORD()
    size = api.GetFileVersionInfoSizeW(str(path), ctypes.byref(ignored))
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(path), 0, size, buffer):
        return None
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not api.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52:
        return None
    numbers = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD * 13)).contents
    if numbers[0] != 0xFEEF04BD:
        return None
    return {"path": str(path), "product_version": [numbers[4] >> 16, numbers[4] & 65535, numbers[5] >> 16, numbers[5] & 65535],
            "file_version": [numbers[2] >> 16, numbers[2] & 65535, numbers[3] >> 16, numbers[3] & 65535],
            "method": "read-only Windows file version resource"}


def reject_links(path, recursive=True):
    for p in (path, *path.parents):
        if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
            raise Stop(f"Refusing linked output path: {p}")
    if recursive and path.is_dir():
        for p in path.iterdir():
            reject_links(p)


@contextmanager
def workspace_lock(parent, preflight):
    """Retained OS lock prevents simultaneous replacement in the same mode."""
    path = parent / (".preflight-dipole.lock" if preflight else ".dipole.lock")
    reject_links(path)
    stream = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        stream.close()


def prepare_outputs(base, parent, preflight):
    """Validate ALL targets and locks before deleting ANY generated output."""
    expected = parent / ("preflight/dipole" if preflight else "dipole")
    reject_links(parent, recursive=False)
    if base.resolve() != expected.resolve():
        raise Stop(f"Output must be the fixed owned directory: {expected}")
    reject_links(base)
    names = ("results", "reports") if preflight else ("project", "results", "reports")
    targets = [base / name for name in names]
    for target in targets:
        reject_links(target)
        if target.resolve().parent != base.resolve() or target.name not in names:
            raise Stop(f"Unsafe cleanup target: {target}")
        if target.exists() and not target.is_dir():
            raise Stop(f"Generated directory is a file: {target}")
    if not preflight:
        project_dir = base / "project"
        for project in project_dir.rglob("*.cst") if project_dir.exists() else []:
            ensure_closed(project)
        for item in project_dir.rglob("*") if project_dir.exists() else []:
            if item.suffix.lower() in {".lok", ".lck", ".lock"}:
                raise Stop(f"Close the existing project first. Lock is present: {item}")
    for target in targets:
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
    for name in ("mcp", "protocol", "cst_logs", "exports", "figures", "work"):
        (base / "results" / name).mkdir()
    (base / "reports/calls.jsonl").write_text("", encoding="utf-8")
    atomic_text(base / "reports/validation.md", "# Thin copper dipole validation\n\nInvocation started. Assessment pending.\n")


def complex_curve(data):
    x, real, imag = data.get("x", []), data.get("real", []), data.get("imag", [])
    if not x or len(x) != len(real) or len(x) != len(imag):
        raise Stop("Full S11 axes are empty or lengths do not match")
    if data.get("sampled") or data.get("total_points", len(x)) != len(x):
        raise Stop("Full S11 unexpectedly contains a preview")
    if not all(math.isfinite(v) for v in [*x, *real, *imag]):
        raise Stop("Full S11 contains nonfinite raw samples")
    if not re.search(r"\bGHz\b", data.get("xlabel", data.get("frequency_unit", "")), re.I):
        raise Incomplete("S11 frequency unit is not verified as GHz")
    return list(x), [complex(r, i) for r, i in zip(real, imag)]


def derive(values):
    magnitudes = [abs(v) for v in values]
    phases = [math.degrees(cmath.phase(v)) if v else None for v in values]
    unwrapped = []
    previous = None
    for phase in phases:
        if phase is None:
            unwrapped.append(None)
            previous = None
        else:
            angle = phase if previous is None else previous + (phase - previous + 180) % 360 - 180
            unwrapped.append(angle)
            previous = angle
    return {
        "mag": magnitudes,
        "db": [20 * math.log10(v) if v else None for v in magnitudes],
        "phase": phases, "unwrapped": unwrapped,
        "vswr": [(1 + v) / (1 - v) if v < 1 else None for v in magnitudes],
        "z": [50 * (1 + v) / (1 - v) if abs(1 - v) > 1e-15 else None for v in values],
        "undefined": "null dB at zero, null phase at zero, null VSWR at |S|>=1, null Z at S=1",
    }


def bandwidth(x, values, threshold_db=-10):
    """Interpolate MAGNITUDE crossings, preserving all intervals and edge flags."""
    if len(x) < 2 or any(b <= a for a, b in zip(x, x[1:])):
        raise Incomplete("Bandwidth requires strictly increasing frequencies without duplicates")
    limit = 10 ** (threshold_db / 20)
    mag = [abs(v) for v in values]
    bands = []
    start = x[0] if mag[0] <= limit else None
    for i in range(1, len(x)):
        before, after = mag[i - 1] <= limit, mag[i] <= limit
        if before != after:
            crossing = x[i - 1] + (x[i] - x[i - 1]) * (limit - mag[i - 1]) / (mag[i] - mag[i - 1])
            if after:
                start = crossing
            else:
                bands.append({"f_lower_ghz": start, "f_upper_ghz": crossing,
                              "lower_truncated": start == x[0], "upper_truncated": False})
                start = None
    if start is not None:
        bands.append({"f_lower_ghz": start, "f_upper_ghz": x[-1],
                      "lower_truncated": start == x[0], "upper_truncated": True})
    return bands


def compare(actual, expected, tolerance=1e-5, wrapped=False):
    if len(actual) != len(expected):
        return False
    for a, b in zip(actual, expected):
        if a is None or b is None:
            if a is not None or b is not None:
                return False
        else:
            try:
                delta = (float(a) - float(b) + 180) % 360 - 180 if wrapped else a - b
                if not math.isfinite(abs(delta)) or abs(delta) > tolerance:
                    return False
            except (ValueError, TypeError):
                return False
    return True


def read_text(path):
    raw = path.read_bytes()
    return raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig", errors="replace")


def touchstone(path):
    header, rows, tokens = None, [], []
    for line in read_text(path).splitlines():
        line = line.split("!", 1)[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            header = line[1:].upper().split()
        elif line.startswith("["):
            raise Incomplete("Touchstone 2.0 route needs an explicit parser")
        else:
            tokens.extend(float(v.replace("D", "E")) for v in line.split())
    if header is None or len(header) < 5 or header[1] != "S" or header[3] != "R":
        raise Incomplete("Touchstone option line is absent or unrecognized")
    if path.suffix.lower() != ".s1p" or len(tokens) % 3 or not tokens:
        raise Stop("Touchstone is not a nonempty one-port curve")
    scales = {"HZ": 1e-9, "KHZ": 1e-6, "MHZ": 1e-3, "GHZ": 1}
    if header[0] not in scales or header[2] not in {"RI", "MA", "DB"}:
        raise Incomplete("Unsupported Touchstone units or representation")
    x, values = [], []
    for f, a, b in zip(tokens[::3], tokens[1::3], tokens[2::3]):
        x.append(f * scales[header[0]])
        values.append(complex(a, b) if header[2] == "RI" else cmath.rect(
            a if header[2] == "MA" else 10 ** (a / 20), math.radians(b)))
    if not all(math.isfinite(v) for v in [*x, *(v.real for v in values), *(v.imag for v in values)]):
        raise Stop("Nonfinite Touchstone sample")
    return {"header": header, "frequency_unit": header[0], "representation": header[2],
            "reference_ohm": float(header[4]), "ports": 1, "x": x, "values": values,
            "sample_count": len(x)}


def ascii_table(path):
    rows, headers, delimiter = [], [], None
    for line in read_text(path).splitlines():
        s = line.strip()
        if not s or re.fullmatch(r"[-=_\s]+", s):
            continue
        sep = ";" if ";" in s else "," if "," in s else None
        parts = s.split(sep) if sep else s.split()
        try:
            row = [float(p.replace("D", "E")) for p in parts]
            if len(row) >= 2:
                rows.append(row)
                delimiter = sep or "whitespace"
        except ValueError:
            headers.append(s)
    if not rows:
        raise Incomplete("No numeric rows in ASCII export")
    if len({len(row) for row in rows}) != 1:
        raise Incomplete("Inconsistent numeric column count")
    if not all(math.isfinite(v) for row in rows for v in row):
        raise Incomplete("Nonfinite ASCII values require explicit assessment")
    return {"header": "\n".join(headers), "delimiter": delimiter, "rows": rows,
            "sample_count": len(rows), "columns": len(rows[0]),
            "extension_mismatch": path.suffix.lower() == ".csv" and delimiter != ","}


def ffs_source(path):
    """Inspect native field-source sections without interpreting them as gain."""
    text = read_text(path)
    lines = text.splitlines()
    fields, counts, frequencies, in_fields = [], None, [], False
    for i, line in enumerate(lines):
        lower = line.lower()
        following = next((s.strip() for s in lines[i + 1:] if s.strip() and not s.strip().startswith("//")), "")
        if "phi" in lower and "theta" in lower and ("samples" in lower or "total #" in lower):
            try:
                pair = [int(v) for v in following.split()]
                if len(pair) == 2:
                    counts = pair
            except ValueError:
                pass
        if "frequency" in lower and "power" in lower:
            numbers = []
            for block_line in lines[i + 1:]:
                stripped = block_line.strip()
                if stripped.startswith("//"):
                    break
                if not stripped:
                    continue
                try:
                    numbers.extend(float(v) for v in stripped.split())
                except ValueError as exc:
                    raise Incomplete("FFS power/frequency block contains nonnumeric data") from exc
            if not numbers or len(numbers) % 4:
                raise Incomplete("FFS power/frequency block needs four values per frequency")
            block_frequencies = numbers[3::4]
            if not all(math.isfinite(v) and v > 0 for v in block_frequencies):
                raise Incomplete("FFS frequency must be finite and positive")
            frequencies.extend(block_frequencies)
        elif "frequency" in lower and "hz" in lower:
            try:
                numbers = [float(v) for v in following.split()]
                if numbers:
                    frequencies.append(numbers[-1])
            except ValueError:
                pass
        if "phi" in lower and "theta" in lower and re.search(r"re\s*\(|im\s*\(|e_theta|e_phi", lower):
            in_fields = True
            continue
        if in_fields and line.strip() and not line.strip().startswith("//"):
            try:
                row = [float(v) for v in line.split()]
                if len(row) >= 6:
                    fields.append(row)
            except ValueError:
                in_fields = False
    if not fields:
        raise Incomplete("FFS contains no declared numerical complex vector-field section")
    finite = all(math.isfinite(v) for row in fields for v in row)
    angles = all(0 <= row[0] <= 360 and 0 <= row[1] <= 180 for row in fields)
    return {"format": "native FFS complex field source", "header": "\n".join(s for s in lines if s.strip().startswith("//")),
            "data_type_farfield": bool(re.search(r"(?is)data\s*type[^\n]*\n\s*farfield", text)),
            "declared_phi_theta_counts": counts, "sample_count": len(fields),
            "counts_match": counts is not None and counts[0] * counts[1] == len(fields),
            "frequency_raw_hz": frequencies, "frequency_ghz": [v / 1e9 for v in frequencies],
            "angular_order": "phi, theta", "finite_field_components": finite, "angles_valid": angles,
            "quantity": "complex Etheta and Ephi field components, not gain", "units": "native FFS field-source normalization",
            "source_reuse_tested": False}


def farfield_grid(path, expected_quantity):
    table = ascii_table(path)
    header = table["header"]
    columns = re.findall(r"([^\[\],;\t]+?)\s*\[([^\]]+)\]", header.splitlines()[-1] if header else "")
    # Search each header line because a frequency comment can follow column names.
    for line in header.splitlines():
        candidate = re.findall(r"([^\[\],;\t]+?)\s*\[([^\]]+)\]", line)
        if len(candidate) >= 3 and "theta" in candidate[0][0].lower():
            columns = candidate
            break
    if len(columns) < 3 or "theta" not in columns[0][0].lower() or "phi" not in columns[1][0].lower():
        raise Incomplete("Farfield angular definitions are not proven by the header")
    if not all("deg" in columns[i][1].lower() for i in (0, 1)):
        raise Incomplete("Farfield angle units are unverified")
    name, unit = columns[2]
    native_quantity = {
        "abs(grlz)": "realized_gain",
        "abs(dir.)": "directivity",
    }.get(re.sub(r"\s+", "", name).lower())
    quantity = "realized_gain" if re.search(r"reali[sz]ed\s*gain", name, re.I) else (
        "directivity" if "directivity" in name.lower() else native_quantity or "unknown")
    if quantity != expected_quantity:
        raise Incomplete(f"Expected {expected_quantity}, original header proves {quantity}: {name}")
    if unit.strip().lower() not in {"dbi", "linear", "1"}:
        raise Incomplete(f"Unverified farfield scale: {unit}")
    grid, seam, conflicts = {}, 0, 0
    for row in table["rows"]:
        if len(row) < 3 or not 0 <= row[0] <= 180 or not 0 <= row[1] <= 360:
            raise Incomplete("Angular grid is not theta 0..180 and phi 0..360")
        key = (row[0], row[1] % 360)
        value = row[2] if unit.strip().lower() == "dbi" else (
            10 * math.log10(row[2]) if row[2] > 0 else -math.inf)
        if key in grid:
            seam += 1
            if abs(grid[key] - value) > 1e-5:
                conflicts += 1
        else:
            grid[key] = value
    theta, phi = sorted({k[0] for k in grid}), sorted({k[1] for k in grid})
    if len(theta) < 3 or len(phi) < 2 or len(grid) != len(theta) * len(phi):
        raise Incomplete("Farfield grid is incomplete")
    dt = [b - a for a, b in zip(theta, theta[1:])]
    dp = [b - a for a, b in zip(phi, phi[1:])] + [360 + phi[0] - phi[-1]]
    if theta[0] != 0 or theta[-1] != 180 or max(dt) - min(dt) > 1e-6 or max(dp) - min(dp) > 1e-6:
        raise Incomplete("Farfield sphere coverage or step is incomplete")
    if conflicts:
        raise Stop("Farfield seam duplicates disagree")
    return {"quantity": quantity, "unit": unit, "theta": theta, "phi": phi, "grid": grid,
            "step_theta": dt[0], "step_phi": dp[0], "seam_duplicates": seam,
            "pole_samples": sum(t in (0, 180) for t, p in grid), "sample_count": len(grid),
            "header": header, "delimiter": table["delimiter"]}


def dipole_physics(grid):
    values, theta, phi = grid["grid"], grid["theta"], grid["phi"]
    peak = max(values.values())
    broadside = max(values[(90, p)] for p in phi) if 90 in theta else None
    p0 = min(phi, key=lambda p: min(p, 360 - p))
    cut = [values[(t, p0)] for t in theta]
    maximum = max(cut)
    crossing = []
    for a, b, va, vb in zip(theta, theta[1:], cut, cut[1:]):
        if (va >= maximum - 3) != (vb >= maximum - 3):
            if math.isfinite(va) and math.isfinite(vb):
                crossing.append(a + (b - a) * (maximum - 3 - va) / (vb - va))
    hpbw = crossing[1] - crossing[0] if len(crossing) == 2 else None
    pole = max(values[(t, p)] for t in (0, 180) for p in phi)
    usable = []
    for t, v in zip(theta, cut):
        if t in (0, 180) or v < maximum - 20:
            continue
        radians = math.radians(t)
        ideal = abs(math.cos(math.pi / 2 * math.cos(radians)) / math.sin(radians))
        if ideal > 0:
            usable.append(abs((v - maximum) - 20 * math.log10(ideal)))
    return {"peak_dbi": peak, "hpbw_deg": hpbw, "broadside_peak_db": broadside,
            "max_theta_distance_deg": max(abs(t - 90) for (t, p), v in values.items() if v >= peak - 1e-6),
            "axial_suppression_db": broadside - pole if broadside is not None else None,
            "azimuth_ripple_db": max(values[(90, p)] for p in phi) - min(values[(90, p)] for p in phi) if 90 in theta else None,
            "shape_max_error_db_above_minus20": max(usable) if usable else None,
            "shape_samples": len(usable), "pole_limit_ideal": 0}


def sphere_mean(grid):
    """Integrate unique angular cells, including polar caps and no seam duplication."""
    theta, phi, values = grid["theta"], grid["phi"], grid["grid"]
    total = 0.0
    for i, t in enumerate(theta):
        lo = 0 if i == 0 else (theta[i - 1] + t) / 2
        hi = 180 if i == len(theta) - 1 else (theta[i + 1] + t) / 2
        area = math.cos(math.radians(lo)) - math.cos(math.radians(hi))
        total += area * sum(10 ** (values[(t, p)] / 10) for p in phi) / len(phi)
    return total / 2
