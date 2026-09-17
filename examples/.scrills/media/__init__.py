# /// script
# dependencies = ["pypdf>=6.19,<7"]
# ///
"""
---
name: media
description: Use when a media file needs to become text, or when you need something inside a long document without reading all of it. PDF today - outline, page search, per-page text, all cached; pages with no text layer can be read by Claude. Needs pypdf (scrills install pypdf).
version: 0.1.2
---

Look before reading - these keep a 300-page document out of your context:
  info(path) -> {pages, title, author}
  outline(path) -> [{level, title, page}] - the document's own bookmarks
  search(path, query, limit=20) -> [{page, hits, snippet}] - case-insensitive phrase match that
    tolerates line breaks; one entry per matching page, first snippet shown
Then read only what matters:
  text(path, pages=None, vision=False, vision_usd=1.0) -> the text; pages like "3", "1-5",
    "2,7-9" (1-based)

vision=True: pages whose text layer holds under 32 characters (scans, vector art) are read by
Claude through the subagent scrill - each page cut into its own one-page PDF, one sandboxed
call per page (only the Read tool, confined to the cache folder), 4 at a time, costs tokens.
vision_usd caps the total spend of one call (default $1, None = uncapped): pages that would
pass it are not started, reported in the error, and never cached - pages already read stay
cached and paid for once. Only a page Claude confirms it saw is cached; later text() and search() calls use
it without vision=True.

Everything extracted is cached by content hash under ~/.scrills/.state/media/<sha256>/, so the
second question about a document costs nothing, even after a rename. Delete that folder to
extract again.

As a program: scrills run media <file> [--pages 1-5] [--vision] [--vision-usd usd|none]
| scrills run media info <file> | scrills run media outline <file>
| scrills run media search <file> <query words...>
"""
import hashlib
import json
import os
import re
import shutil
import sys

HOME = os.path.abspath(os.path.expanduser(os.environ.get("SCRILLS_HOME", "").strip() or "~/.scrills"))
CACHE_DIR = os.path.join(HOME, ".state", "media")
MIN_TEXT = 32
CONTEXT = 80
VISION_LIMIT = 4
VISION_MODEL = "sonnet"
VISION_PROMPT = (
    "Use the Read tool on the PDF file {path} - read the whole file, it is a single page. "
    "Transcribe all the text on that page exactly as written, in reading order, keeping its original language. "
    "Set read to true only if you actually saw the page contents; if you could not, set read to false and give the reason as text."
)
VISION_SCHEMA = {
    "type": "object",
    "properties": {"read": {"type": "boolean"}, "text": {"type": "string"}},
    "required": ["read", "text"],
}


class _Pdf:
    def __init__(self, path):
        self.path = path
        self._reader = None

    def reader(self):
        if self._reader is None:
            try:
                from pypdf import PdfReader
            except ImportError:
                raise RuntimeError("media: pypdf is missing - scrills install pypdf") from None
            try:
                self._reader = PdfReader(self.path)
            except Exception as error:
                raise RuntimeError(f"media: pypdf can't read {self.path}: {error}") from None
        return self._reader

    def count(self):
        return len(self.reader().pages)

    def page_text(self, index):
        try:
            return self.reader().pages[index].extract_text() or ""
        except Exception as error:
            raise RuntimeError(f"media: pypdf failed extracting page {index + 1} of {self.path}: {error}") from None

    def meta(self):
        meta = self.reader().metadata
        found = {}
        for key in ("title", "author"):
            value = getattr(meta, key, None) if meta is not None else None
            if value:
                found[key] = str(value)
        return found

    def page_file(self, index, count, target):
        if count == 1:
            shutil.copyfile(self.path, target)
            return
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_page(self.reader().pages[index])
        with open(target, "wb") as handle:
            writer.write(handle)

    def outline(self):
        reader = self.reader()
        entries = []

        def walk(items, level):
            for item in items:
                if isinstance(item, list):
                    walk(item, level + 1)
                    continue
                try:
                    number = reader.get_destination_page_number(item)
                except Exception:
                    number = -1
                entries.append({"level": level, "title": str(getattr(item, "title", "") or ""), "page": number + 1 if number >= 0 else None})

        walk(reader.outline, 0)
        return entries


HANDLERS = {".pdf": _Pdf}


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _sha256(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class _Doc:
    def __init__(self, path):
        self.path = os.path.abspath(os.path.expanduser(str(path)))
        extension = os.path.splitext(self.path)[1].lower()
        if extension not in HANDLERS:
            known = ", ".join(sorted(HANDLERS))
            raise ValueError(f"media: no handler for '{extension or self.path}' yet (handles: {known})")
        self.handler = HANDLERS[extension](self.path)
        self.folder = os.path.join(CACHE_DIR, _sha256(self.path))

    def _cached_json(self, name, compute):
        path = os.path.join(self.folder, name)
        raw = _read(path)
        if raw is not None:
            try:
                return json.loads(raw)
            except ValueError:
                pass
        value = compute()
        _write(path, json.dumps(value, ensure_ascii=False))
        return value

    def info(self):
        return self._cached_json("meta.json", lambda: {"pages": self.handler.count(), **self.handler.meta()})

    def outline(self):
        return self._cached_json("outline.json", self.handler.outline)

    def count(self):
        return self.info()["pages"]

    def layer(self, index):
        path = os.path.join(self.folder, f"page-{index + 1}.txt")
        cached = _read(path)
        if cached is None:
            cached = self.handler.page_text(index)
            _write(path, cached)
        return cached

    def seen_path(self, index):
        return os.path.join(self.folder, f"page-{index + 1}.vision.txt")

    def blank(self, index):
        return len(self.layer(index).strip()) < MIN_TEXT

    def page(self, index):
        layer = self.layer(index)
        if len(layer.strip()) >= MIN_TEXT:
            return layer
        seen = _read(self.seen_path(index))
        return layer if seen is None else seen

    def see(self, indexes, vision_usd):
        todo = [index for index in indexes if self.blank(index) and _read(self.seen_path(index)) is None]
        if not todo:
            return
        try:
            from scrills import subagent
        except ImportError:
            raise RuntimeError("media: vision=True needs the subagent scrill in your library") from None
        os.makedirs(self.folder, exist_ok=True)
        count = self.count()
        files = []
        try:
            for index in todo:
                target = os.path.join(self.folder, f"page-{index + 1}.{os.getpid()}.pdf")
                files.append(target)
                self.handler.page_file(index, count, target)
            answers = subagent.map(
                [VISION_PROMPT.format(path=target) for target in files],
                limit=VISION_LIMIT,
                total_usd=vision_usd,
                model=VISION_MODEL,
                schema=VISION_SCHEMA,
                tools="Read",
                allowed_tools="Read",
                cwd=self.folder,
                extra=("--restricted",),
            )
        finally:
            for target in files:
                try:
                    os.remove(target)
                except OSError:
                    pass
        failed = []
        refused = 0
        for index, answer in zip(todo, answers):
            if isinstance(answer, Exception):
                failed.append((index + 1, str(answer)))
                refused += str(answer).startswith("subagent: not started")
            elif not isinstance(answer, dict) or not answer.get("read"):
                reason = answer.get("text") if isinstance(answer, dict) else answer
                failed.append((index + 1, f"Claude could not read it: {str(reason)[:300]}"))
            else:
                _write(self.seen_path(index), str(answer.get("text") or ""))
        if failed:
            page, reason = failed[0]
            budget = ""
            if refused:
                budget = f" - {refused} page(s) were never started: vision_usd={vision_usd} caps this call's total spend (CLI: --vision-usd, none = uncapped)"
            raise RuntimeError(f"media: vision failed on {len(failed)} page(s), nothing cached for them; page {page}: {reason}{budget}")


def _page_numbers(spec, count):
    if spec is None:
        return list(range(count))
    chosen = []
    for part in str(spec).split(","):
        piece = part.strip()
        if not piece:
            continue
        if "-" in piece:
            begin, end = piece.split("-", 1)
            if int(begin) > int(end):
                raise ValueError(f"media: page range {piece} is reversed - write {end.strip()}-{begin.strip()}")
            chosen.extend(range(int(begin) - 1, int(end)))
        else:
            chosen.append(int(piece) - 1)
    bad = [number + 1 for number in chosen if number < 0 or number >= count]
    if bad:
        raise ValueError(f"media: page {bad[0]} out of range (the file has {count})")
    return chosen


def info(path):
    """{pages, title, author} - no page text extracted."""
    return dict(_Doc(path).info())


def outline(path):
    """The bookmarks as [{level, title, page}], level 0 at the top; [] when the file has none."""
    return list(_Doc(path).outline())


def search(path, query, limit=20):
    """Pages containing the phrase, as [{page, hits, snippet}], at most `limit` pages."""
    words = str(query).split()
    if not words:
        raise ValueError("media: search needs a non-empty query")
    pattern = re.compile(r"\s+".join(re.escape(word) for word in words), re.IGNORECASE)
    doc = _Doc(path)
    found = []
    for index in range(doc.count()):
        body = doc.page(index)
        matches = list(pattern.finditer(body))
        if not matches:
            continue
        first = matches[0]
        window = body[max(0, first.start() - CONTEXT) : first.end() + CONTEXT]
        found.append({"page": index + 1, "hits": len(matches), "snippet": " ".join(window.split())})
        if len(found) >= limit:
            break
    return found


def text(path, pages=None, vision=False, vision_usd=1.0):
    """The text of the chosen pages, blank-line separated; vision=True lets Claude read textless pages, spending at most vision_usd."""
    doc = _Doc(path)
    indexes = _page_numbers(pages, doc.count())
    if vision:
        doc.see(indexes, vision_usd)
    return "\n\n".join(doc.page(index) for index in indexes)


def main():
    args = sys.argv[1:]
    verb = args.pop(0) if args and args[0] in ("info", "outline", "search", "text") else "text"
    try:
        if verb == "info":
            if len(args) != 1:
                print("usage: scrills run media info <file>", file=sys.stderr)
                return 2
            for key, value in info(args[0]).items():
                print(f"{key}: {value}")
            return 0
        if verb == "outline":
            if len(args) != 1:
                print("usage: scrills run media outline <file>", file=sys.stderr)
                return 2
            entries = outline(args[0])
            if not entries:
                print("media: no outline in this file")
            for entry in entries:
                page = entry["page"] if entry["page"] is not None else "?"
                print(f"{'  ' * entry['level']}{entry['title']}  p.{page}")
            return 0
        if verb == "search":
            if len(args) < 2:
                print("usage: scrills run media search <file> <query words...>", file=sys.stderr)
                return 2
            hits = search(args[0], " ".join(args[1:]))
            if not hits:
                print("media: no match")
            for hit in hits:
                print(f"p.{hit['page']} ({hit['hits']}): {hit['snippet']}")
            return 0
        pages, vision, vision_usd = None, False, 1.0
        if "--vision" in args:
            args.remove("--vision")
            vision = True
        if "--vision-usd" in args:
            where = args.index("--vision-usd")
            if where + 1 >= len(args):
                print("media: --vision-usd needs a number", file=sys.stderr)
                return 2
            raw = args[where + 1]
            try:
                vision_usd = None if raw.lower() == "none" else float(raw)
            except ValueError:
                print(f"media: --vision-usd takes a number or none, got {raw}", file=sys.stderr)
                return 2
            del args[where : where + 2]
        if "--pages" in args:
            where = args.index("--pages")
            if where + 1 >= len(args):
                print("media: --pages needs a value like 1-5", file=sys.stderr)
                return 2
            pages = args[where + 1]
            del args[where : where + 2]
        if len(args) != 1:
            print("usage: scrills run media <file> [--pages 1-5] [--vision] [--vision-usd usd|none]", file=sys.stderr)
            return 2
        body = text(args[0], pages, vision, vision_usd)
        print(body)
        if not body.strip() and not vision:
            print("media: no text layer on these pages - add --vision to have Claude read them (costs tokens)", file=sys.stderr)
    except (RuntimeError, ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0
