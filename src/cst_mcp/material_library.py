"""Read native CST material definitions and serialize them without a CST connection."""

from __future__ import annotations

import codecs
import difflib
import math
import re
from pathlib import Path

from cst_mcp.validators import ValidationError, validate_name, validate_vba_input
from cst_mcp.vba_builder import VBABuilder
from cst_mcp.vba_safety import vba_escape

_SECTION = re.compile(r"\[([^\[\]]+)\]")
_COMMAND = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)(?:[ \t]+(.*))?")
_ARGUMENT = re.compile(r'"((?:[^"]|"")*)"')


_MAX_SIMILAR = 5


class LibraryEntryNotFound(ValidationError):
    """No loadable entry has this name; carries close names from the same directory."""

    def __init__(self, message: str, directory: Path | None, similar: list[str]) -> None:
        super().__init__(message)
        self.directory = directory
        self.similar = similar


def is_loadable_library_name(library_name: object) -> bool:
    """Whether ``library_name`` is a single entry name the loader accepts."""
    return not (
        not isinstance(library_name, str)
        or not library_name.strip()
        or library_name in {".", ".."}
        or library_name != library_name.strip()
        or library_name.endswith(".")
        or re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', library_name)
    )


def library_directory(cst_path: Path | None, library_path: str | None = None) -> Path:
    """The material directory ``cst_load_material`` reads, resolved and checked."""
    if library_path is not None:
        if not isinstance(library_path, str) or not library_path.strip():
            raise ValidationError("library_path must be a nonempty directory path")
        directory = Path(library_path)
    elif cst_path is not None:
        directory = cst_path / "Library" / "Materials"
    else:
        raise ValidationError(
            "CST installation path is unavailable. Configure CST_PATH or supply library_path."
        )

    if not directory.is_dir():
        raise ValidationError(
            f"Material library directory does not exist or is not a directory: {directory}"
        )
    return directory.resolve(strict=True)


def library_cst_version(directory: Path) -> str | None:
    """The CST release year in the directory's install path, e.g. ``2025``."""
    for part in reversed(directory.parts):
        match = re.fullmatch(r"cst studio suite (\d{4})", part.strip(), re.IGNORECASE)
        if match:
            return match[1]
    return None


def library_entry_names(directory: Path) -> list[str]:
    """Every ``*.mtd`` file name in the directory (without extension), sorted."""
    names = [
        path.name[: -len(".mtd")]
        for path in directory.iterdir()
        if path.name.lower().endswith(".mtd") and path.is_file()
    ]
    return sorted(names, key=lambda name: (name.casefold(), name))


def _words(text: str) -> list[str]:
    return re.findall(r"[0-9a-z]+", text.casefold())


def _compact(text: str) -> str:
    return "".join(_words(text))


def library_name_matches(query: str, library_name: str) -> bool:
    """Case-insensitive: every word of ``query`` occurs in the name, ignoring punctuation.

    "RT/duroid 5870" therefore matches "Rogers RT-duroid 5870 (lossy)", and "FR4" "FR-4".
    """
    words = _words(query)
    name = _compact(library_name)
    return bool(words) and all(word in name for word in words)


def similar_library_names(query: str, names: list[str], limit: int = _MAX_SIMILAR) -> list[str]:
    """Up to ``limit`` names close to ``query``: word matches first, then spelling."""
    target = " ".join(_words(query))
    if not target:
        return []

    def closeness(name: str) -> float:
        return difflib.SequenceMatcher(None, target, " ".join(_words(name))).ratio()

    matches = sorted((n for n in names if library_name_matches(query, n)), key=closeness)[::-1]
    if len(matches) < limit:
        by_spelling = sorted(
            ((closeness(n), n) for n in names if n not in matches), key=lambda pair: -pair[0]
        )
        matches += [n for score, n in by_spelling if score >= 0.6][: limit - len(matches)]
    return matches[:limit]


def _not_found(message: str, library_name: object, directory: Path | None) -> LibraryEntryNotFound:
    similar: list[str] = []
    if directory is not None and isinstance(library_name, str):
        try:
            names = [n for n in library_entry_names(directory) if is_loadable_library_name(n)]
        except OSError:
            names = []
        similar = similar_library_names(library_name, names)
    if similar:
        message += " Close library names: " + ", ".join(f"'{n}'" for n in similar) + "."
    return LibraryEntryNotFound(message, directory, similar)


def resolve_material_file(
    library_name: str, cst_path: Path | None, library_path: str | None = None
) -> Path:
    """Resolve exactly one native entry within the selected material directory."""
    if not is_loadable_library_name(library_name):
        try:
            directory = library_directory(cst_path, library_path)
        except ValidationError:
            directory = None
        raise _not_found(
            "library_name must be a single native library entry name (the .mtd file name "
            "without extension, as cst_list_library_materials returns it).",
            library_name,
            directory,
        )

    directory = library_directory(cst_path, library_path)
    material_file = directory / f"{library_name}.mtd"
    if not material_file.is_file():
        raise _not_found(
            f"Native material file does not exist or is not a file: {material_file}",
            library_name,
            directory,
        )
    resolved = material_file.resolve(strict=True)
    if resolved.parent != directory:
        raise ValidationError(
            f"Native material file must remain within the library directory: {material_file}"
        )
    return resolved


def _decode_lines(material_file: Path) -> list[str]:
    """Decode strictly (UTF-8, or UTF-16/32 by BOM) and split into lines."""
    try:
        data = material_file.read_bytes()
    except OSError as exc:
        raise ValidationError(f"Cannot read native material file {material_file}: {exc}") from exc

    encoding = "utf-8-sig"
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    try:
        text = data.decode(encoding)
    except UnicodeError as exc:
        raise ValidationError(f"Cannot decode native material file {material_file}: {exc}") from exc
    # Normalize only Windows line endings, leaving other control characters for validation.
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _read_definition(material_file: Path) -> list[tuple[int, str]]:
    """Decode strictly and extract the single Definition section, excluding metadata."""
    definition: list[tuple[int, str]] = []
    found = False
    in_definition = False
    for number, line in enumerate(_decode_lines(material_file), 1):
        section = _SECTION.fullmatch(line.strip(" \t"))
        if section:
            in_definition = section[1].casefold() == "definition"
            if in_definition:
                if found:
                    raise ValidationError(f"Multiple [Definition] sections in {material_file}")
                found = True
        elif in_definition:
            definition.append((number, line))
    if not found:
        raise ValidationError(f"Missing [Definition] section in {material_file}")
    validate_vba_input("\n".join(line for _, line in definition))
    return definition


def _parse_command(line: str) -> tuple[str, list[str]]:
    """Accept one dot command with only quoted VBA string arguments."""
    command = _COMMAND.fullmatch(line)
    if command is None:
        raise ValidationError("Expected a native .Method command with quoted arguments")
    method, argument_text = command.groups()
    validate_vba_input(method)
    arguments: list[str] = []
    if argument_text is None:
        return method, arguments

    position = 0
    while True:
        argument = _ARGUMENT.match(argument_text, position)
        if argument is None:
            raise ValidationError("Expected a quoted argument, including after each comma")
        value = argument[1].replace('""', '"')
        vba_escape(value, "native material argument")
        arguments.append(value)
        position = argument.end()
        while position < len(argument_text) and argument_text[position] in " \t":
            position += 1
        if position == len(argument_text):
            return method, arguments
        if argument_text[position] != ",":
            raise ValidationError("Only comma-separated quoted arguments are supported")
        position += 1
        while position < len(argument_text) and argument_text[position] in " \t":
            position += 1


def load_material_vba(
    name: str,
    library_name: str,
    *,
    cst_path: Path | None,
    library_path: str | None = None,
) -> str:
    """Build one Material block preserving all native property commands in order."""
    validate_name(name, "material name")
    material_file = resolve_material_file(library_name, cst_path, library_path)
    builder = VBABuilder("Material").call("Reset").set("Name", name).set("Folder", "")
    created = False
    has_properties = False
    for number, source in _read_definition(material_file):
        line = source.strip(" \t")
        if not line or line.startswith("'"):
            continue
        try:
            method, arguments = _parse_command(line)
            key = method.casefold()
            if created:
                raise ValidationError(".Create must be the final command in [Definition]")
            if key in {"name", "folder"}:
                if len(arguments) != 1:
                    raise ValidationError(f".{method} requires exactly one quoted argument")
                # The leading Name and Folder replace all source identity settings.
                continue
            if key == "reset":
                if arguments or has_properties:
                    raise ValidationError(
                        ".Reset must have no arguments and precede all properties"
                    )
                continue
            if key in {"rename", "delete"}:
                raise ValidationError(f".{method} is not a material definition command")
            if key == "create":
                if arguments or not has_properties:
                    raise ValidationError(
                        ".Create must have no arguments and follow material properties"
                    )
                created = True
            else:
                has_properties = True
            if arguments:
                builder.call_with_args(method, *arguments)
            else:
                builder.call(method)
        except (ValidationError, ValueError) as exc:
            raise ValidationError(
                f"Invalid native material definition in {material_file} at line {number}: {exc}"
            ) from exc
    if not created:
        raise ValidationError(
            f"Native material definition must terminate with .Create: {material_file}"
        )
    return validate_vba_input(builder.build())


# ---------------------------------------------------------------------------
# Library listing: properties as the native file defines them
# ---------------------------------------------------------------------------

_FREQUENCY_SCALE = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9, "thz": 1e12, "phz": 1e15}
# ``.Type`` values as the library files spell them, with the keys MCP 12 uses.
_LIBRARY_TYPES = {
    "normal": "normal",
    "lossy metal": "lossy_metal",
    "pec": "pec",
    "anisotropic": "anisotropic",
}
_MAX_DESCRIPTION = 300
_EM_FIELDS = ("epsilon_r", "mu_r", "conductivity", "magnetic_conductivity")
_LOSS_FIELDS = ("tan_d", "tan_d_given", "tan_d_freq", "tan_d_model", "dispersion_model")


def _definition_blocks(lines: list[str], material_file: Path) -> tuple[list[dict], list[str]]:
    """Split [Definition] into ``.FrqType`` blocks of {command: last arguments}.

    A light read for listing only: ``load_material_vba`` still validates the whole
    definition when the entry is loaded.
    """
    blocks: list[dict] = []
    description: list[str] = []
    section = None
    seen_definition = False
    unit = None  # material frequency unit; a setting, so it carries into later blocks
    for line in lines:
        header = _SECTION.fullmatch(line.strip(" \t"))
        if header:
            section = header[1].casefold()
            if section == "definition":
                if seen_definition:
                    raise ValidationError(f"Multiple [Definition] sections in {material_file}")
                seen_definition = True
            continue
        if section == "description":
            if line.strip():
                description.append(line.strip())
            continue
        if section != "definition":
            continue
        command = _COMMAND.fullmatch(line.strip(" \t"))
        if command is None:
            continue
        key = command[1].casefold()
        if key in {"reset", "name", "folder", "create"}:
            continue  # identity and framing, not properties
        arguments = [a.replace('""', '"') for a in _ARGUMENT.findall(command[2] or "")]
        if key == "frqtype" or not blocks:
            blocks.append({"frqtype": None, "commands": {}, "fit_points": 0, "unit": unit})
        block = blocks[-1]
        if key == "frqtype":
            block["frqtype"] = arguments[0] if arguments else None
        elif key == "setmaterialunit" and arguments:
            unit = block["unit"] = arguments[0]
        elif key == "materialunit" and len(arguments) == 2:
            if arguments[0].casefold() == "frequency":
                unit = block["unit"] = arguments[1]
        elif key == "adddispersionfittingvalueeps":
            block["fit_points"] += 1
        else:
            block["commands"][key] = arguments
    if not seen_definition:
        raise ValidationError(f"Missing [Definition] section in {material_file}")
    return blocks, description


def _choose_block(blocks: list[dict]) -> dict:
    """The block high-frequency solvers use: ``hf`` over ``all`` over the first one."""
    for wanted in ("hf", "all"):
        for block in blocks:
            if (block["frqtype"] or "").casefold() == wanted:
                return block
    return blocks[0]


class _BlockReader:
    """Typed reads from one block, recording why a value is missing."""

    def __init__(self, block: dict) -> None:
        self.block = block
        self.unavailable: dict[str, str] = {}

    def text(self, key: str) -> str | None:
        values = self.block["commands"].get(key)
        return values[0] if values else None

    def number(self, field: str, *keys: str) -> float | None:
        for key in keys:
            raw = self.text(key)
            if raw is None:
                continue
            try:
                value = float(raw)
            except ValueError:
                self.unavailable[field] = f"Not a number in the file: {raw!r}"
                return None
            if not math.isfinite(value):
                self.unavailable[field] = f"Not a finite number in the file: {raw!r}"
                return None
            return value
        self.unavailable[field] = "Not set in the file."
        return None

    def absent(self, entry: dict, fields: tuple[str, ...], reason: str) -> None:
        for field in fields:
            entry[field] = None
            self.unavailable[field] = reason


def read_library_material(material_file: Path) -> dict:
    """The EM properties a native ``.mtd`` file defines, with MCP 12's field names.

    A value the file does not give is ``None`` with the reason in ``unavailable``.
    Nothing is taken from the bundled database.
    """
    blocks, description = _definition_blocks(_decode_lines(material_file), material_file)
    if not blocks:
        raise ValidationError(f"Empty [Definition] section in {material_file}")
    block = _choose_block(blocks)
    read = _BlockReader(block)

    raw_type = read.text("type")
    kind = _LIBRARY_TYPES.get((raw_type or "").casefold(), "other" if raw_type else None)
    entry: dict = {"type": kind, "raw_type": raw_type}
    if raw_type is None:
        read.unavailable["type"] = "The file sets no .Type."

    if kind == "pec":
        read.absent(entry, _EM_FIELDS + _LOSS_FIELDS, "Not applicable to PEC materials.")
    else:
        if kind == "lossy_metal":
            read.absent(entry, ("epsilon_r",), "Not applicable to Lossy metal materials.")
        else:
            entry["epsilon_r"] = read.number("epsilon_r", "epsilon")
        entry["mu_r"] = read.number("mu_r", "mu")
        entry["conductivity"] = read.number("conductivity", "kappa", "sigma")
        entry["magnetic_conductivity"] = read.number("magnetic_conductivity", "kappam", "sigmam")
        for field, key in (("epsilon_r", "epsilon"), ("mu_r", "mu")):
            axes = {axis: read.text(f"{key}{axis}") for axis in "xyz"}
            if all(axes.values()):
                try:
                    entry[f"{field}_axes"] = {a: float(v) for a, v in axes.items()}
                except ValueError:
                    pass
        if kind == "lossy_metal":
            read.absent(entry, _LOSS_FIELDS, "Not applicable to Lossy metal materials.")
        else:
            _loss_fields(entry, read)

    entry["frequency_block"] = block["frqtype"]
    if len(blocks) > 1:
        entry["frequency_blocks"] = [b["frqtype"] for b in blocks]
    if description:
        joined = " ".join(description)
        if len(joined) > _MAX_DESCRIPTION:
            joined = joined[: _MAX_DESCRIPTION - 3] + "..."
        entry["description"] = joined
    entry["unavailable"] = read.unavailable
    return entry


def _loss_fields(entry: dict, read: _BlockReader) -> None:
    """tan(delta), its frequency in Hz and model, and the permittivity dispersion model."""
    model = read.text("dispmodeleps")
    if model is not None and model.casefold() != "none":
        entry["dispersion_model"] = model
    elif (read.text("usegeneraldispersioneps") or "").casefold() == "true":
        entry["dispersion_model"] = "general"
        entry["dispersion_fit_points"] = read.block["fit_points"]
    elif model is not None:
        entry["dispersion_model"] = "none"
    else:
        read.absent(entry, ("dispersion_model",), "The file sets no .DispModelEps.")

    given = (read.text("tandgiven") or "").casefold()
    entry["tan_d_given"] = {"true": True, "false": False}.get(given)
    if entry["tan_d_given"] is None:
        read.unavailable["tan_d_given"] = "The file sets no .TanDGiven."
    if entry["tan_d_given"] is True:
        entry["tan_d"] = read.number("tan_d", "tand")
        entry["tan_d_model"] = read.text("tandmodel")
        frequency = read.number("tan_d_freq", "tandfreq")
        unit = read.block["unit"]
        scale = _FREQUENCY_SCALE.get((unit or "").casefold())
        if frequency is not None and scale is None:
            read.unavailable["tan_d_freq"] = f"Unknown material frequency unit: {unit!r}"
            frequency = None
        entry["tan_d_freq"] = None if frequency is None else frequency * scale
    else:
        where = (
            "the dispersion model"
            if entry.get("dispersion_model") not in (None, "none")
            else "conductivity"
        )
        read.absent(
            entry,
            ("tan_d", "tan_d_freq", "tan_d_model"),
            f"TanDGiven is not True, so CST ignores TanD; any loss is in {where}.",
        )
