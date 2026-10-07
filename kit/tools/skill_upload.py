#!/usr/bin/env python3
"""The upload copy of a harness's SKILL.md, for claude.ai and the Skills API.

    python3 kit/tools/skill_upload.py SKILL_DIR OUT_DIR   write OUT_DIR/<name>/ and OUT_DIR/<name>.zip
    python3 kit/tools/skill_upload.py --check SKILL_DIR   exit 1 naming why the upload would refuse it

A harness's SKILL.md serves two kinds of host. A host adapter (the
playbook's hosts/ README) reads top-level `version` and `type` and keys of
its own. The upload (claude.ai Settings > Features, the Skills API, the
Agent Skills spec) refuses any top-level key outside name, description,
license, compatibility, metadata and allowed-tools. So the repository keeps
the frontmatter the adapter reads, and this tool writes the copy that is
uploaded: `name`, `description`, and `version` and `type` moved under
`metadata` as strings. The body is the same bytes.

The copy is a folder named after `name` (the spec: the name matches its
folder) holding SKILL.md and every file SKILL.md links to by a relative
path inside SKILL_DIR, plus a zip of that folder (claude.ai takes a zip).
A link to a folder, a missing file or a path outside SKILL_DIR is not
copied and is named in the output. The tool never shortens or rewrites a
value: a name or description the upload would refuse is refused here,
before any file is written. An existing copy is refused, never replaced.

The YAML is read line by line (stdlib only), the subset a SKILL.md
frontmatter uses: plain, single- or double-quoted scalars (a double-quoted
one with JSON's escapes), `>` and `|` blocks, indented continuation lines.

Stdlib only; runs without a bound harness.

Test: kit/tests/test_skill_upload.py.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

NAME = "SKILL.md"
# The top-level keys the upload accepts (the Agent Skills spec).
UPLOAD_KEYS = ("name", "description", "license", "compatibility",
               "metadata", "allowed-tools")
# The keys moved under `metadata` in the copy.
MOVED_KEYS = ("version", "type")
NAME_MAX = 64
DESCRIPTION_MAX = 1024
RESERVED = ("anthropic", "claude")
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
XML_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
ZIP_TIME = (1980, 1, 1, 0, 0, 0)


class Refused(Exception):
    """The source cannot become an upload copy; the message says why."""


def split(text: str) -> tuple[list[str], str]:
    """(frontmatter lines, body) of a SKILL.md; Refused without a --- block."""
    m = re.match(r"---\r?\n(.*?)\r?\n---\r?(?:\n|$)", text, re.S)
    if not m:
        raise Refused(f"{NAME} has no --- frontmatter block at its top")
    return m.group(1).splitlines(), text[m.end():]


def _scalar(head: str, rest: list[str], key: str) -> str:
    if re.fullmatch(r"[>|][+-]?", head):
        return ("\n" if head[0] == "|" else " ").join(rest)
    raw = " ".join([head, *rest]).strip()
    if raw.startswith('"'):
        try:
            value, end = json.JSONDecoder().raw_decode(raw)
        except ValueError:
            raise Refused(f"{key}: a double-quoted value this tool cannot read "
                          f"(only JSON's escapes): {raw[:60]!r}") from None
        if raw[end:].strip() and not raw[end:].strip().startswith("#"):
            raise Refused(f"{key}: text after the closing quote")
        return value
    if raw.startswith("'"):
        m = re.fullmatch(r"'((?:[^']|'')*)'\s*(#.*)?", raw)
        if not m:
            raise Refused(f"{key}: an unclosed single-quoted value")
        return m.group(1).replace("''", "'")
    return re.sub(r"\s+#.*$", "", raw)


def fields(lines: list[str]) -> dict[str, str | None]:
    """Every top-level key: its scalar value, or None for a block (a mapping
    or a list under it)."""
    out: dict[str, str | None] = {}
    for i, line in enumerate(lines):
        m = re.match(r"([A-Za-z0-9_.-]+):(?:\s+(.*))?$", line)
        if not m:
            continue
        rest = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and not nxt[0].isspace():
                break
            if nxt.strip() and not nxt.strip().startswith("#"):
                rest.append(nxt.strip())
        key, head = m.group(1), (m.group(2) or "").strip()
        if head and not head.startswith("#"):
            out[key] = _scalar(head, rest, key)
        elif rest and re.match(r"-(\s|$)|[A-Za-z0-9_.-]+:(\s|$)", rest[0]):
            out[key] = None
        else:
            out[key] = " ".join(rest)
    return out


def problems(text: str) -> list[str]:
    """Every reason the upload would refuse this SKILL.md's copy (empty =
    it uploads)."""
    try:
        f = fields(split(text)[0])
    except Refused as e:
        return [str(e)]
    out = []
    name, desc = f.get("name"), f.get("description")
    if not name:
        out.append("name: missing")
    else:
        if len(name) > NAME_MAX:
            out.append(f"name: {len(name)} characters, the upload takes at most {NAME_MAX}")
        if not NAME_RE.match(name):
            out.append(f"name: {name!r} is not lowercase letters, digits and single hyphens")
        out += [f"name: contains the reserved word {w!r}" for w in RESERVED if w in name]
    if not desc:
        out.append("description: missing or empty")
    else:
        if len(desc) > DESCRIPTION_MAX:
            out.append(f"description: {len(desc)} characters, the upload takes at most "
                       f"{DESCRIPTION_MAX}; shorten it in {NAME} (this tool never cuts it)")
        if XML_TAG.search(desc):
            out.append(f"description: an XML tag {XML_TAG.search(desc).group(0)!r}")
    out += [f"{k}: a block, not a plain value" for k in MOVED_KEYS
            if k in f and f[k] is None]
    return out


def upload_text(text: str) -> str:
    """The copy's SKILL.md: name, description, metadata {version, type}, then
    the same body. Refused while problems(text) is not empty."""
    found = problems(text)
    if found:
        raise Refused("; ".join(found))
    lines, body = split(text)
    f = fields(lines)
    head = [f"name: {f['name']}",
            f"description: {json.dumps(f['description'], ensure_ascii=False)}"]
    meta = [f"  {k}: {json.dumps(f[k], ensure_ascii=False)}" for k in MOVED_KEYS if f.get(k)]
    if meta:
        head += ["metadata:", *meta]
    return "---\n" + "\n".join(head) + "\n---\n" + body


def links(skill_dir: Path, body: str) -> tuple[list[str], list[str]]:
    """(files to copy, links not copied with the reason): SKILL.md's relative
    links, each once, in the order they appear."""
    root = Path(skill_dir).resolve()
    copy, skipped, seen = [], [], set()
    for target in LINK.findall(body):
        rel = target.split("#", 1)[0]
        if not rel or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", rel) or rel in seen:
            continue
        seen.add(rel)
        path = (root / rel).resolve()
        if rel.startswith("/") or not path.is_relative_to(root):
            skipped.append(f"{rel}: outside the skill folder")
        elif path.is_dir():
            skipped.append(f"{rel}: a folder")
        elif not path.is_file():
            skipped.append(f"{rel}: no such file")
        elif path.relative_to(root).as_posix() != NAME:
            copy.append(path.relative_to(root).as_posix())
    return copy, skipped


def write(skill_dir: Path, out_dir: Path) -> dict:
    """Write out_dir/<name>/ and out_dir/<name>.zip; return what was written."""
    skill_dir, out_dir = Path(skill_dir), Path(out_dir)
    source = skill_dir / NAME
    if not source.is_file():
        raise Refused(f"{source}: no such file")
    text = source.read_text(encoding="utf-8")
    copy_text = upload_text(text)
    name = fields(split(text)[0])["name"]
    folder, archive = out_dir / name, out_dir / f"{name}.zip"
    for p in (folder, archive):
        if p.exists():
            raise Refused(f"{p} exists: remove it first (a copy is never replaced)")
    files, skipped = links(skill_dir, split(text)[1])
    folder.mkdir(parents=True)
    (folder / NAME).write_text(copy_text, encoding="utf-8")
    for rel in files:
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(skill_dir / rel, folder / rel)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in [NAME, *sorted(files)]:
            info = zipfile.ZipInfo(f"{name}/{rel}", ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, (folder / rel).read_bytes())
    return {"folder": str(folder), "zip": str(archive), "files": [NAME, *files],
            "not_copied": skipped}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    checking = "--check" in args
    rest = [a for a in args if a != "--check"]
    if len(rest) != (1 if checking else 2) or not Path(rest[0]).is_dir():
        print("usage: skill_upload.py SKILL_DIR OUT_DIR | --check SKILL_DIR", file=sys.stderr)
        return 2
    if checking:
        source = Path(rest[0]) / NAME
        found = problems(source.read_text(encoding="utf-8")) if source.is_file() \
            else [f"{source}: no such file"]
        for p in found:
            print(p)
        print(f"{source}: {'the upload would refuse its copy' if found else 'its copy uploads'}")
        return 1 if found else 0
    try:
        done = write(Path(rest[0]), Path(rest[1]))
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(done["zip"])
    for rel in done["files"]:
        print(f"  copied: {rel}")
    for why in done["not_copied"]:
        print(f"  not copied: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
