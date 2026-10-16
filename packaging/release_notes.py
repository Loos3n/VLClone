"""Release notes for a tag: the commits since the previous v* tag, as Markdown.

    python packaging/release_notes.py v1.1.0 --out notes.md

Only v* tags count as releases (the manual ``build-N`` pre-releases are ignored).
Merge commits are skipped; each commit's subject becomes a bullet.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_URL = "https://github.com/Loos3n/VLClone"


def git(*args: str) -> str:
    # git writes UTF-8; without encoding= Windows would decode it with its ANSI code page (mojibake).
    return subprocess.run(["git", *args], check=True, capture_output=True, encoding="utf-8").stdout.strip()


def previous_tag(tag: str) -> str | None:
    try:
        return git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", f"{tag}^")
    except subprocess.CalledProcessError:
        return None  # the first release


def commits(tag: str, since: str | None) -> list[tuple[str, str]]:
    span = f"{since}..{tag}" if since else tag
    out = git("log", "--no-merges", "--pretty=format:%h%x09%s", span)
    return [tuple(line.split("\t", 1)) for line in out.splitlines() if "\t" in line]


def render(tag: str, since: str | None, items: list[tuple[str, str]]) -> str:
    version = tag.lstrip("vV")
    lines = [f"## VLClone {version}", ""]
    if since:
        lines.append(f"Changes since {since} ([full diff]({REPO_URL}/compare/{since}...{tag})):")
    else:
        lines.append("First release. Changes:")
    lines.append("")
    skip = ("Merge ", "Co-Authored-By")
    lines += [f"- {subject} ({sha})" for sha, subject in items if not subject.startswith(skip)] or ["- No changes."]
    lines += ["", "### Downloads", "",
              f"* `VLClone-{version}-Setup.exe`: installer (Start menu, optional desktop icon, opt-in file "
              "types and Explorer entries, uninstaller).",
              f"* `VLClone-{version}-win64-portable.zip`: unzip anywhere and run `VLClone.exe`.",
              "", "Both bundle libVLC, so VLC does not need to be installed. The exe is unsigned: Windows "
              "SmartScreen may ask once (*More info → Run anyway*).", ""]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    tag = argv[0]
    since = previous_tag(tag)
    notes = render(tag, since, commits(tag, since))
    if "--out" in argv:
        # Written as UTF-8 here: Windows' console code page cannot encode the notes, and
        # PowerShell's ">" would save them as UTF-16.
        out = Path(argv[argv.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(notes, encoding="utf-8")
    else:
        sys.stdout.buffer.write(notes.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
