"""Static sanity checks of a described model setup (boundaries, ports, monitors).

Pure functions: nothing is read from or written to CST.

Key rule (CST help, special_solvopt_boundary_conditions_boundaries): with the
TD solver, geometry and materials touching an ``open`` boundary are extended
into the PML and become virtually infinite.  ``open`` without added space puts
the domain boundary directly on the structure's bounding box, so whatever
reaches that extreme (board, grounds, feed line) touches the PML.  A waveguide
port on such a face then feeds an infinite line: part of the accepted power is
absorbed in the PML, is neither radiated nor counted as loss, and
efficiency/gain (and the apparent S11 bandwidth) become unreliable.
"""

from __future__ import annotations

from typing import Any

FACES = ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")
_ORIENT_TO_FACE = {
    "xmin": "x_min",
    "xmax": "x_max",
    "ymin": "y_min",
    "ymax": "y_max",
    "zmin": "z_min",
    "zmax": "z_max",
}
_FACE_AXIS = {
    "x_min": (0, "min"),
    "x_max": (0, "max"),
    "y_min": (1, "min"),
    "y_max": (1, "max"),
    "z_min": (2, "min"),
    "z_max": (2, "max"),
}
# Boundary types that touch the structure and extend it into the PML.
TOUCHING_OPEN = {"open"}

PML_EXTENSION = (
    "CST extends geometry touching an 'open' boundary into the PML (virtually infinite). "
    "'open' adds no space, so structure at that extreme touches the PML and behaves as an "
    "infinite line: power absorbed there is neither radiated nor counted as loss, making "
    "efficiency/gain unreliable and S11 artificially broadband."
)
PORT_FIX = (
    "Use 'expanded open' on this face and feed through a closed "
    "structure (coax/SMA, e.g. cst_add_sma_edge_connector) with an internal waveguide port "
    "(PortOnBound False) on the coax back face."
)
OPEN_FACE_CONSEQUENCE = (
    "Power absorbed in the PML is neither radiated nor counted as loss, so efficiency and "
    "gain are unreliable and S11 looks artificially broadband."
)


def _norm_face(key: str) -> str:
    k = str(key).strip().lower().replace("-", "_")
    if k in _ORIENT_TO_FACE:
        return _ORIENT_TO_FACE[k]
    if k in FACES:
        return k
    raise ValueError(f"unknown face '{key}' (use x_min..z_max or xmin..zmax)")


def open_faces(boundaries: dict[str, str]) -> list[str]:
    """Faces set to plain 'open', in ``FACES`` order."""
    return [f for f in FACES if str(boundaries.get(f, "")).strip().lower() in TOUCHING_OPEN]


def open_face_warning(faces: list[str]) -> dict[str, Any]:
    """One warning for all faces set to plain 'open' (structure touches the PML there)."""
    return {
        "severity": "warning",
        "code": "open_boundary_touches_structure",
        "faces": list(faces),
        "message": "'open' adds no space: the domain ends at the structure's bounding box, so "
        f"the structure touches faces {', '.join(faces)} and is extended into the PML there. "
        "Use 'expanded open' to add space between the structure and the boundary. "
        + OPEN_FACE_CONSEQUENCE,
    }


def open_face_warnings(boundaries: dict[str, str]) -> list[dict[str, Any]]:
    """At most one warning listing the faces set to plain 'open'."""
    faces = open_faces(boundaries)
    return [open_face_warning(faces)] if faces else []


def _touches(
    bbox: dict[str, float] | None, face: str, plane: float | None, tol: float
) -> bool | None:
    if not bbox:
        return None
    axis, side = _FACE_AXIS[face]
    key = ("x", "y", "z")[axis] + side
    extreme = bbox.get(key)
    if extreme is None:
        return None
    if plane is None:
        return True  # the domain face of an 'open' boundary is the bbox extreme itself
    return abs(float(extreme) - float(plane)) <= tol


def check_setup(
    boundaries: dict[str, str] | None,
    ports: list[dict[str, Any]] | None = None,
    *,
    structure_bbox: dict[str, float] | None = None,
    power_loss_1d: bool | None = None,
    farfield_frequencies: list[float] | None = None,
    field_monitor_frequencies: list[float] | None = None,
    tol: float = 1e-6,
) -> dict[str, Any]:
    """Return ``{"status", "issues", "ok"}`` for the described setup."""
    bnd = {}
    for k, v in (boundaries or {}).items():
        bnd[_norm_face(k)] = str(v)
    issues: list[dict[str, Any]] = []
    port_faces: set[str] = set()
    for i, p in enumerate(ports or []):
        orient = p.get("orientation")
        if not orient:
            continue
        face = _norm_face(orient)
        btype = str(bnd.get(face, "")).strip().lower()
        pn = p.get("port_number", i + 1)
        kind = str(p.get("type", "waveguide")).lower()
        on_bound = p.get("port_on_bound")
        plane = p.get("plane")
        if kind == "waveguide" and btype in TOUCHING_OPEN:
            port_faces.add(face)
            touches = _touches(structure_bbox, face, plane, tol)
            if touches is None:
                touches = True if on_bound in (None, True) else None
            if touches is False:
                issues.append(
                    {
                        "severity": "info",
                        "code": "port_on_open_face_clear",
                        "face": face,
                        "port": pn,
                        "message": f"Port {pn} faces the 'open' {face} boundary but its plane is not at the "
                        "structure extreme; still check that nothing else touches that face.",
                    }
                )
            else:
                issues.append(
                    {
                        "severity": "error" if touches else "warning",
                        "code": "waveguide_port_on_open_boundary",
                        "face": face,
                        "port": pn,
                        "message": (
                            f"Waveguide port {pn} sits on the 'open' {face} face"
                            + (
                                " and the structure touches that face. "
                                if touches
                                else " (cannot tell whether structure touches it; pass structure_bbox). "
                            )
                            + PML_EXTENSION
                        ),
                        "fix": PORT_FIX,
                    }
                )
        if (
            kind == "waveguide"
            and on_bound is True
            and btype in {"expanded open", "open (add space)"}
        ):
            issues.append(
                {
                    "severity": "warning",
                    "code": "port_on_bound_with_added_space",
                    "face": face,
                    "port": pn,
                    "message": f"Port {pn} uses PortOnBound True on a face with added space: CST snaps it to "
                    "the expanded domain boundary, away from the feed. Use PortOnBound False with "
                    "the port on the feed/coax face.",
                }
            )
    touching = [
        f
        for f in open_faces(bnd)
        if f not in port_faces  # already reported with the port
        and _touches(structure_bbox, f, None, tol) is not False
    ]
    if touching:
        issues.append(open_face_warning(touching))
    ff = sorted({float(f) for f in farfield_frequencies or []})
    fm = sorted({float(f) for f in field_monitor_frequencies or []})
    if power_loss_1d is not True and ff:
        missing = [f for f in ff if all(abs(f - g) > 1e-9 for g in fm)]
        if missing:
            issues.append(
                {
                    # A fact about the result data, not a setup requirement: info, so it
                    # does not set ``ok`` to False for a complete setup.
                    "severity": "info",
                    "code": "losses_missing_at_farfield_frequencies",
                    "frequencies_ghz": missing,
                    "message": "Loss in Dielectrics/Metals is computed only at 3D field-monitor "
                    f"frequencies, so no 1D loss data exists at {missing} GHz unless "
                    "Solver.ActivatePowerLoss1DMonitor is on.",
                }
            )
    severities = {i["severity"] for i in issues}
    return {
        "status": "ok",
        "ok": not ({"error", "warning"} & severities),
        "issues": issues,
        "boundaries": bnd,
        "checked": {"ports": len(ports or []), "structure_bbox": bool(structure_bbox)},
    }
