"""Read native CST material definitions and serialize them without a CST connection."""

from __future__ import annotations

import codecs
import re
from pathlib import Path

from cst_mcp.validators import ValidationError, validate_name, validate_vba_input
from cst_mcp.vba_builder import VBABuilder
from cst_mcp.vba_safety import vba_escape

_SECTION = re.compile(r"\[([^\[\]]+)\]")
_COMMAND = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)(?:[ \t]+(.*))?")
_ARGUMENT = re.compile(r'"((?:[^"]|"")*)"')


def resolve_material_file(
    library_name: str, cst_path: Path | None, library_path: str | None = None
) -> Path:
    """Resolve exactly one native entry within the selected material directory."""
    if (
        not isinstance(library_name, str)
        or not library_name.strip()
        or library_name in {".", ".."}
        or library_name != library_name.strip()
        or library_name.endswith(".")
        or re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', library_name)
    ):
        raise ValidationError("library_name must be a single native library entry name")

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
    directory = directory.resolve(strict=True)
    material_file = directory / f"{library_name}.mtd"
    if not material_file.is_file():
        raise ValidationError(
            f"Native material file does not exist or is not a file: {material_file}"
        )
    resolved = material_file.resolve(strict=True)
    if resolved.parent != directory:
        raise ValidationError(
            f"Native material file must remain within the library directory: {material_file}"
        )
    return resolved


def _read_definition(material_file: Path) -> list[tuple[int, str]]:
    """Decode strictly and extract the single Definition section, excluding metadata."""
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

    definition: list[tuple[int, str]] = []
    found = False
    in_definition = False
    # Normalize only Windows line endings, leaving other control characters for validation.
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for number, line in enumerate(lines, 1):
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
                    raise ValidationError(".Reset must have no arguments and precede all properties")
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
