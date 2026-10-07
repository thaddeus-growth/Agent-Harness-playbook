#!/usr/bin/env python3
"""kit/tools/skill_upload.py: the upload copy of a SKILL.md.

  [1] the frontmatter hosts/zylos/README.md documents (Zylos keys and all)
      becomes a copy whose top-level keys are name, description and
      metadata only, every one the upload accepts; version and type are
      strings under metadata; the body is the same bytes
  [2] the folder is named after `name`; it and the zip hold SKILL.md and
      the files it links to inside the skill folder; a link to a folder, a
      missing file, a path outside, a URL or an anchor is not copied, and
      the first three are named; the zip is the same bytes on a re-run
  [3] what the upload refuses is refused before any file is written, never
      cut: a description over 1024 characters or with an XML tag, a name
      that is too long, not lowercase-and-hyphens or holds a reserved word,
      no frontmatter; an existing copy is never replaced
  [4] the scalar forms a frontmatter uses: double quotes with escapes,
      single quotes, plain with a comment, `>` and `|` blocks
  [5] the CLI: write, --check exiting 1 with each reason and 0 when clean,
      usage exit 2
"""

import json
import re
import sys
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kit.testing.check import capture, check, finish, tmp_dir  # noqa: E402
from kit.tools import skill_upload as su  # noqa: E402

BODY = ("\n# shop-harness\n\nRead [README.md](README.md) first; recipes in "
        "[references/workflows.md](references/workflows.md#daily), the guide in "
        "[docs](docs/), [the design](../DESIGN.md), [gone](missing.md), "
        "[site](https://example.com/x.md) and [here](#rules).\n")


def zylos_example() -> str:
    """The frontmatter the Zylos README documents, as a SKILL.md."""
    text = (ROOT / "hosts/zylos/README.md").read_text(encoding="utf-8")
    block = text.split("## SKILL.md frontmatter", 1)[1]
    m = re.search(r"```yaml\n(---\n.*?\n---)\n```", block, re.S)
    return m.group(1) + "\n" + BODY


def skill(text: str, extra: dict | None = None) -> Path:
    d = Path(tmp_dir("skill-"))
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    for rel, body in {"README.md": "readme\n", "references/workflows.md": "w\n",
                      "docs/a.md": "a\n", **(extra or {})}.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(body, encoding="utf-8")
    return d


def top_keys(text: str) -> list[str]:
    head = text.split("\n---\n", 1)[0]
    return re.findall(r"^([A-Za-z0-9_-]+):", head, re.M)


def with_desc(desc: str, name: str = "shop-harness") -> str:
    return (f"---\nname: {name}\ndescription: {json.dumps(desc)}\n"
            f"version: 0.1.0\ntype: capability\n---\n{BODY}")


def main() -> int:
    print("[1] the documented Zylos frontmatter becomes an upload copy")
    src = zylos_example()
    check("the example carries the Zylos keys",
          {"bin", "lifecycle", "config", "next-steps"} <= set(top_keys(src)), top_keys(src))
    check("the example itself is refused by the upload's key rule",
          set(top_keys(src)) - set(su.UPLOAD_KEYS), top_keys(src))
    check("the example's copy has no problems", su.problems(src) == [], su.problems(src))
    out = su.upload_text(src)
    check("the copy's top-level keys: name, description, metadata",
          top_keys(out) == ["name", "description", "metadata"], top_keys(out))
    check("every key the upload accepts", set(top_keys(out)) <= set(su.UPLOAD_KEYS))
    check("version and type are strings under metadata",
          '\nmetadata:\n  version: "0.1.0"\n  type: "capability"\n---\n' in out, out[:400])
    f = su.fields(su.split(out)[0])
    check("name and description read back unchanged",
          f["name"] == "shop-harness"
          and f["description"] == su.fields(su.split(src)[0])["description"], f)
    check("the body is the same bytes", su.split(out)[1] == su.split(src)[1] == BODY)

    print("[2] the folder, the linked files and the zip")
    d = skill(src)
    o = Path(tmp_dir("out-"))
    done = su.write(d, o)
    folder = o / "shop-harness"
    check("the folder is named after name", Path(done["folder"]) == folder and folder.is_dir())
    check("SKILL.md and the linked files inside the skill folder",
          done["files"] == ["SKILL.md", "README.md", "references/workflows.md"], done["files"])
    check("the copy's SKILL.md is upload_text()",
          (folder / "SKILL.md").read_text(encoding="utf-8") == out)
    check("a linked file is the same bytes",
          (folder / "references/workflows.md").read_text() == "w\n")
    check("a folder, a path outside and a missing file are named, not copied",
          done["not_copied"] == ["docs/: a folder", "../DESIGN.md: outside the skill folder",
                                 "missing.md: no such file"], done["not_copied"])
    check("nothing else is copied",
          sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
          == ["README.md", "SKILL.md", "references/workflows.md"])
    with zipfile.ZipFile(o / "shop-harness.zip") as z:
        names = z.namelist()
        check("the zip holds the folder", names == ["shop-harness/SKILL.md", "shop-harness/README.md",
                                                    "shop-harness/references/workflows.md"], names)
        check("the zip's SKILL.md is the copy",
              z.read("shop-harness/SKILL.md").decode() == out)
    o2 = Path(tmp_dir("out-"))
    su.write(d, o2)
    check("a re-run writes the same zip bytes",
          (o / "shop-harness.zip").read_bytes() == (o2 / "shop-harness.zip").read_bytes())

    print("[3] what the upload refuses is refused here, never cut")
    cases = {
        "a description over 1024 characters": (with_desc("x" * 1025), "1025 characters"),
        "a description with an XML tag": (with_desc("Use it <b>now</b>."), "XML tag"),
        "a name over 64 characters": (with_desc("ok", "a" * 65), "65 characters"),
        "a name with capitals": (with_desc("ok", "Shop-Harness"), "lowercase"),
        "a name with a double hyphen": (with_desc("ok", "shop--harness"), "lowercase"),
        "a reserved word in the name": (with_desc("ok", "claude-shop"), "'claude'"),
        "no description": ("---\nname: shop-harness\n---\nbody\n", "description: missing"),
        "no frontmatter": ("# shop-harness\n", "no --- frontmatter"),
    }
    for label, (text, want) in cases.items():
        found = su.problems(text)
        check(f"{label}: named", any(want in p for p in found), found)
        o = Path(tmp_dir("out-"))
        try:
            su.write(skill(text), o)
            refused = False
        except su.Refused as e:
            refused = want in str(e)
        check(f"{label}: refused, nothing written", refused and not any(o.iterdir()))
    check("exactly 1024 characters uploads", su.problems(with_desc("x" * 1024)) == [])
    d = skill(with_desc("ok"))
    o = Path(tmp_dir("out-"))
    su.write(d, o)
    try:
        su.write(d, o)
        again = "written twice"
    except su.Refused as e:
        again = str(e)
    check("an existing copy is refused, never replaced", "exists" in again, again)

    print("[4] the scalar forms")
    forms = {
        'description: "a \\"quoted\\" 場景 line"': 'a "quoted" 場景 line',
        "description: 'it''s single'": "it's single",
        "description: plain text # a comment": "plain text",
        "description: >\n  folded over\n  two lines": "folded over two lines",
        "description: |\n  kept\n  lines": "kept\nlines",
        'description: "continued\n  on a second line"': "continued on a second line",
    }
    for line, want in forms.items():
        got = su.fields(su.split(f"---\nname: n\n{line}\nversion: 1.0.0\n---\n")[0])
        check(f"{line.splitlines()[0]!r} reads {want!r}",
              got.get("description") == want and got.get("version") == "1.0.0", got)
    copy = su.upload_text(with_desc('say "hi" \\ 場景'))
    check("an escaped description is written back as one valid string",
          su.fields(su.split(copy)[0])["description"] == 'say "hi" \\ 場景', copy[:200])
    check("a block under version or type is named",
          any("version: a block" in p for p in
              su.problems("---\nname: n\ndescription: d\nversion:\n  major: 1\n---\n")))

    print("[5] the CLI")
    d = skill(src)
    o = Path(tmp_dir("out-"))
    code, stdout, _ = capture(su.main, [str(d), str(o)])
    check("write: exit 0, prints the zip and what was and was not copied",
          code == 0 and stdout.splitlines()[0].endswith("shop-harness.zip")
          and "copied: README.md" in stdout and "not copied: missing.md" in stdout, stdout)
    code, stdout, _ = capture(su.main, ["--check", str(d)])
    check("--check on a clean skill: exit 0", code == 0 and "its copy uploads" in stdout, stdout)
    code, stdout, _ = capture(su.main, ["--check", str(skill(with_desc("x" * 2000)))])
    check("--check on a refused skill: exit 1 with the reason",
          code == 1 and "2000 characters" in stdout, stdout)
    code, _, stderr = capture(su.main, [str(skill(with_desc("x" * 2000))), str(o)])
    check("write on a refused skill: exit 1, 'refused:'", code == 1 and "refused:" in stderr, stderr)
    code, _, _ = capture(su.main, [str(d)])
    check("a missing OUT_DIR: usage, exit 2", code == 2)

    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
