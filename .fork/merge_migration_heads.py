#!/usr/bin/env python3
"""Join the fork's and upstream's Alembic lines with a merge revision.

Fork migrations (Authentik, plugNmeet) sit in one line after the upstream
head they were written against. When an upstream release adds migrations of
its own, the graph gets two heads: upstream's test_migration_graph fails and
`alembic upgrade head` refuses to run. This adds an empty merge revision
whose parents are every head, which is Alembic's own answer: existing
databases (at the fork head) then apply only the new upstream migrations,
and new databases apply everything.

Stdlib only (the sync job has no project environment). Idempotent: with a
single head it does nothing. Usage: merge_migration_heads.py <versions dir>
Prints the new file's path when it writes one.
"""

from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path


def _assignments(path: Path) -> dict[str, object]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, object] = {}
    for node in tree.body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and target.id in ("revision", "down_revision"):
            found[target.id] = ast.literal_eval(value)
    return found


def heads(versions: Path) -> list[str]:
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in sorted(versions.glob("*.py")):
        info = _assignments(path)
        rev = info.get("revision")
        if not isinstance(rev, str):
            continue
        revisions.add(rev)
        down = info.get("down_revision")
        if isinstance(down, str):
            parents.add(down)
        elif isinstance(down, (tuple, list)):
            parents.update(d for d in down if isinstance(d, str))
    return sorted(revisions - parents)


TEMPLATE = '''"""Merge the fork's migrations with upstream's ({joined})

Written by .fork/merge_migration_heads.py during an upstream sync. Empty on
purpose: it only joins the two lines so the graph has a single head again.

Revision ID: {revision}
Revises: {joined}
"""
from typing import Sequence, Union

revision: str = '{revision}'
down_revision: Union[str, Sequence[str], None] = {parents!r}
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
'''


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: merge_migration_heads.py <migrations/versions dir>", file=sys.stderr)
        return 64
    versions = Path(argv[1])
    current = heads(versions)
    if len(current) <= 1:
        return 0
    revision = "fm" + hashlib.sha256(",".join(current).encode()).hexdigest()[:10]
    target = versions / f"{revision}_fork_merge_heads.py"
    target.write_text(
        TEMPLATE.format(revision=revision, joined=", ".join(current), parents=tuple(current)),
        encoding="utf-8",
    )
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
