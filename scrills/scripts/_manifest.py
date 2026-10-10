"""Read README metadata and entry source without importing a scrill.

parse() is the single restricted-frontmatter reader used by list, read and the
registry. read_manual() never substitutes another file or a module docstring.
read_source() decodes Python source and reports syntax errors without hiding it.
version_of() samples only README at finalization; it does not claim code provenance.
"""
import ast
import io
import json
import os
import re
import stat
import tokenize
from dataclasses import dataclass, field


KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")
FIELDS = ("name", "description", "version", "compatibility")


@dataclass
class Metadata:
    fields: dict = field(default_factory=dict)
    locations: dict = field(default_factory=dict)
    notices: list = field(default_factory=list)
    description: str = ""


@dataclass
class Manual:
    path: str
    text: object
    metadata: Metadata
    error: object = None


def diagnostic(origin, line, message):
    return f"{origin}:{line}: ({message})"


def scalar(value):
    """Decode one line; single-quoted YAML or JSON-style double quotes, no coercion."""
    value = value.strip()
    if value.startswith('"'):
        try:
            decoded, end = json.JSONDecoder().raw_decode(value)
        except ValueError:
            return None, "unsupported quoted value - use one line and JSON-style escapes"
        tail = value[end:]
    elif value.startswith("'"):
        pieces = []
        end = 1
        while end < len(value):
            char = value[end]
            end += 1
            if char != "'":
                pieces.append(char)
            elif end < len(value) and value[end] == "'":
                pieces.append("'")
                end += 1
            else:
                break
        else:
            return None, "unclosed quoted value"
        decoded, tail = "".join(pieces), value[end:]
    else:
        decoded = re.split(r"(?<!\S)#", value, maxsplit=1)[0].rstrip()
        tail = ""
        if decoded.startswith(("|", ">")):
            return None, "uses a YAML block scalar - scrills frontmatter takes one-line values"
        if decoded.startswith(("[", "{")) or re.match(r"^[-?](?:\s|$)", decoded):
            return None, "is a structured value - scrills frontmatter takes one-line values"
        if decoded.startswith(("!", "&", "*")):
            return None, "uses an unsupported YAML tag, anchor or alias"
    if tail.strip() and not (tail[:1].isspace() and tail.lstrip().startswith("#")):
        return None, "unexpected text after a quoted value"
    if not decoded.strip():
        return None, "is empty - supply a one-line value"
    if any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF or char in "\x7f\x85\u2028\u2029" for char in decoded):
        return None, "must be a one-line Unicode value without control characters or lone surrogates"
    return decoded, None


def meaningful(lines):
    return [(number, raw) for number, raw in lines if raw.strip() and not raw.lstrip().startswith("#")]


def blocks(lines, indent):
    """Group a key with all its continuation lines; blank lines cannot end a value."""
    groups = []
    for number, raw in lines:
        depth = len(raw) - len(raw.lstrip(" "))
        match = KEY.match(raw[indent:]) if depth == indent else None
        if match:
            groups.append((number, match.group(1), match.group(2), []))
        elif groups:
            groups[-1][3].append((number, raw))
        else:
            groups.append((number, None, "", [(number, raw)]))
    return groups


def parse(text, origin="README.md"):
    """Extract supported fields and located notices, never partial/guessed values."""
    result = Metadata()
    lines = list(enumerate(text.lstrip("\ufeff").splitlines(), 1))
    start = next((i for i, (_, raw) in enumerate(lines) if raw.strip()), None)
    if start is None or lines[start][1].rstrip() != "---":
        result.description = lines[start][1].strip() if start is not None else "(empty README.md)"
        result.notices.append(diagnostic(origin, lines[start][0] if start is not None else 1,
                                         "no frontmatter - open README.md with ---, name/description/metadata.version, ---"))
        return result
    end = next((i for i in range(start + 1, len(lines)) if lines[i][1].rstrip() == "---"), None)
    if end is None:
        result.notices.append(diagnostic(origin, lines[start][0], "frontmatter never closes - missing the second ---"))
        return result
    declared, values, invalid = {}, {}, set()

    def complain(line, message):
        result.notices.append(diagnostic(origin, line, message))

    def take(name, line, raw, continuation):
        if name in declared:
            invalid.add(name)
            complain(line, f"duplicate {name} - declare it once")
        else:
            declared[name] = line
        value, problem = scalar(raw)
        if problem:
            invalid.add(name)
            complain(line, f"{name} {problem}")
        if continuation:
            invalid.add(name)
            complain(continuation[0][0], f"{name} continues on the next line - scrills frontmatter takes one-line values")
        if name not in invalid:
            values[name] = value
        else:
            values.pop(name, None)

    metadata_line = None
    metadata_invalid = False
    for line, key, raw, children in blocks(meaningful(lines[start + 1:end]), 0):
        if key in FIELDS:
            take(key, line, raw, children)
        elif key == "metadata":
            if metadata_line is not None:
                metadata_invalid = True
                complain(line, "duplicate metadata - declare the mapping once")
            metadata_line = line
            if raw.strip() and not raw.lstrip().startswith("#"):
                metadata_invalid = True
                complain(line, "metadata must be a mapping on indented lines")
                continue
            if not children:
                continue
            indent = len(children[0][1]) - len(children[0][1].lstrip(" "))
            if indent == 0 or any(len(item) - len(item.lstrip(" ")) < indent for _, item in children):
                metadata_invalid = True
                complain(line, "metadata has inconsistent indentation")
                continue
            for child_line, child_key, child_raw, descendants in blocks(children, indent):
                if child_key == "version":
                    take("metadata.version", child_line, child_raw, descendants)
                elif child_key is None:
                    metadata_invalid = True
                    complain(child_line, "metadata must contain indented key: value entries")
        elif key is None:
            complain(line, "expected a top-level key: value - indentation uses spaces")
    if metadata_invalid:
        values.pop("metadata.version", None)
    result.fields = {key: value for key, value in values.items() if key in FIELDS}
    if "version" in declared:
        if "metadata.version" in declared:
            choice = "the top-level value wins" if "version" in values else "the top-level version is invalid; metadata.version ignored"
            complain(declared["version"], "version declared in both places - keep one; " + choice)
    elif "metadata.version" in values:
        result.fields["version"] = values["metadata.version"]
    for key in ("description", "version"):
        if key not in result.fields:
            complain(declared.get(key, lines[start][0]), f"no {key} in frontmatter")
    result.locations = declared
    result.description = result.fields.get("description", "")
    return result


def read_document(path, python=False):
    """Read a regular file (symlinks allowed), with strict decoding and no FIFO wait."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise OSError("not a regular file")
            data = handle.read()
        encoding = tokenize.detect_encoding(io.BytesIO(data).readline)[0] if python else "utf-8-sig"
        text = data.decode(encoding)
        return text.replace("\r\n", "\n").replace("\r", "\n"), None
    except (OSError, UnicodeError, LookupError, SyntaxError) as error:
        return None, f"{path}: cannot read: {error}"


def read_manual(entry):
    """README is required; neither SKILL.md nor __doc__ can substitute for it."""
    path = os.path.join(os.path.dirname(entry), "README.md")
    text, error = read_document(path)
    if error is not None:
        error += "; a readable README.md is required"
        return Manual(path, None, Metadata(notices=[error], description="(README.md is required or unreadable)"), error)
    metadata = parse(text, path)
    name = os.path.basename(os.path.dirname(entry))
    stated = metadata.fields.get("name")
    if stated and stated != name:
        metadata.notices.append(diagnostic(path, metadata.locations["name"],
            f"frontmatter name '{stated}' differs from folder '{name}' - the folder name is the import name"))
    return Manual(path, text, metadata)


def read_source(entry):
    """Return the complete source even if it cannot compile; decoding failure is fatal."""
    text, error = read_document(entry, python=True)
    notices = [error] if error is not None else []
    if text is not None:
        try:
            ast.parse(text, filename=entry)
        except (SyntaxError, ValueError) as problem:
            notices.append(diagnostic(entry, getattr(problem, "lineno", None) or 1, f"doesn't parse: {problem}"))
    return text, notices, error


def version_of(module):
    """The declared README version sampled at finalization, not executed-code identity."""
    entry = getattr(module, "__file__", None)
    if not isinstance(entry, str) or os.path.basename(entry) != "__init__.py":
        return None
    return read_manual(entry).metadata.fields.get("version")
