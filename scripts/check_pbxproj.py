#!/usr/bin/env python3
"""Pre-flight validator for hand-written project.pbxproj files.

Catches the three failure modes that cost the most CI iterations:
  1. duplicate object IDs          -> xcodebuild crashes ("unrecognized selector")
  2. dangling object references    -> build-phase/dependency resolution crashes
  3. unbalanced braces/parens      -> parse failure

Usage: check_pbxproj.py path/to/project.pbxproj [more.pbxproj ...]
Exit code 0 = clean, 1 = problems found.

Optionally pass --synced-info-plist to remind about the
PBXFileSystemSynchronizedBuildFileExceptionSet requirement when the synced
source folder contains an Info.plist (checked only if the source dir is next
to the pbxproj).
"""
import collections
import re
import sys
from pathlib import Path

SECTION_RE = re.compile(r"^/\* Begin (\S+) section \*/", re.M)
DEF_RE = re.compile(r"^\t\t([0-9A-F]{24})(?: /\* (.+?) \*/)? = \{", re.M)
REF_RE = re.compile(r"([0-9A-F]{24})")


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return text


def check(path: Path) -> list[str]:
    problems: list[str] = []
    raw = path.read_text(encoding="utf-8")
    text = strip_comments(raw)

    # 3. brace/paren balance
    for open_c, close_c in [("{", "}"), ("(", ")")]:
        if text.count(open_c) != text.count(close_c):
            problems.append(
                f"unbalanced {open_c}{close_c}: {text.count(open_c)} vs {text.count(close_c)}"
            )

    # header sanity
    if "archiveVersion" not in text or "rootObject" not in text:
        problems.append("missing archiveVersion/rootObject — not a pbxproj?")

    # 1. duplicate IDs (top-level object definitions)
    defs = DEF_RE.findall(raw)
    id_counts = collections.Counter(fid for fid, _ in defs)
    for fid, count in id_counts.items():
        if count > 1:
            names = [name for i, name in defs if i == fid]
            problems.append(f"duplicate object ID {fid}: {names}")

    # objectVersion sanity for synchronized groups
    m = re.search(r"objectVersion = (\d+);", text)
    if m and int(m.group(1)) >= 77:
        if "PBXFileSystemSynchronizedRootGroup" in text and "preferredProjectObjectVersion" not in text:
            problems.append(
                "objectVersion >= 77 with synchronized groups but preferredProjectObjectVersion missing"
            )

    # 2. dangling references
    defined = set(id_counts) | {m.group(1) for m in [re.search(r"^\t([0-9A-F]{24}) /\* Project object \*/ = \{", raw, re.M)] if m}
    # rootObject id appears as `rootObject = ID`
    root = re.search(r"rootObject = ([0-9A-F]{24})", text)
    if root:
        defined.add(root.group(1))
    # comment-decorated references may appear anywhere
    all_ids = set(REF_RE.findall(raw))
    dangling = all_ids - defined
    if dangling:
        for fid in sorted(dangling):
            # find first line mentioning it for context
            for line in raw.splitlines():
                if fid in line:
                    problems.append(f"dangling reference {fid} in: {line.strip()[:90]}")
                    break

    return problems


def check_synced_info_plist(path: Path) -> list[str]:
    """If a PBXFileSystemSynchronizedRootGroup folder contains Info.plist but has
    no exception set, Xcode will fail with 'Multiple commands produce Info.plist'."""
    problems: list[str] = []
    raw = path.read_text(encoding="utf-8")
    if "PBXFileSystemSynchronizedBuildFileExceptionSet" not in raw:
        for group in re.finditer(
            r"PBXFileSystemSynchronizedRootGroup[^}]*?path = (\w+);", raw, re.S
        ):
            src_dir = path.parent / group.group(1)
            if (src_dir / "Info.plist").is_file():
                problems.append(
                    f"synced group '{group.group(1)}' contains Info.plist but project has no "
                    "PBXFileSystemSynchronizedBuildFileExceptionSet — expect 'Multiple commands produce Info.plist'"
                )
    return problems


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 1
    ok = True
    for arg in args:
        p = Path(arg)
        if not p.is_file():
            print(f"{p}: NOT FOUND")
            ok = False
            continue
        problems = check(p)
        if "--synced-info-plist" in sys.argv:
            problems += check_synced_info_plist(p)
        if problems:
            ok = False
            print(f"{p}:")
            for item in problems:
                print(f"  - {item}")
        else:
            print(f"{p}: OK")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
