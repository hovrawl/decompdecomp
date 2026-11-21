"""What decompdecomp keeps on disk, for the user to review: the project folders, and the ones nothing owns any more.

A project's folder can stay behind: removing a project without ticking "delete the folder", a file another program
holds open (Windows refuses to delete it), a folder import that was cancelled, an upload kept to resume an import.
Settings lists them here so the user can delete them (or open the folder and do it by hand) instead of hunting
through `~/Decomp`. Only folders inside the projects folder are ever listed or deleted, and never one a project, a
running or resumable import, or the app's own data (`.decomp`) uses.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

SIZE_FILES_CAP = 200_000  # a folder with more files than this is reported as "at least" (walking it can take minutes)
APP_DIR = ".decomp"


def _inside(path: Path, parent: Path) -> bool:
    try:
        return path == parent or path.resolve().is_relative_to(parent.resolve())
    except OSError:
        return False


def folder_size(path: Path) -> tuple[int, bool]:
    """(bytes, exact): the size of a folder, stopping at SIZE_FILES_CAP files."""
    total = seen = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for name in files:
            seen += 1
            if seen > SIZE_FILES_CAP:
                return total, False
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total, True


def remove_tree(path: Path) -> list[str]:
    """Delete a folder; returns what could not be deleted (empty: all gone). Read-only files are made writable first:
    a git checkout's objects are, and `rmtree` stopped on them on Windows."""
    failed: list[str] = []

    def onexc(function, name, _exc):
        try:
            os.chmod(name, stat.S_IWRITE)
            function(name)
        except OSError:
            failed.append(str(name))

    shutil.rmtree(path, onexc=onexc)
    return failed


def prune_empty(path: Path, workspace: Path) -> None:
    """After a project's folder goes, the empty folders a folder import made around it (`x-batch/pc/`) go too."""
    parent = path.parent
    while parent != workspace and _inside(parent, workspace) and not parent.name.startswith("."):
        try:
            parent.rmdir()  # (only when empty)
        except OSError:
            return
        parent = parent.parent


def scan(net, imports) -> dict:
    """Every folder of the projects folder, and what owns it: {"root", "rows": [{path, kind, ...}]}.
    kind: "project" (slug, title), "import" (an import in progress or resumable owns it), "leftover" (nothing does:
    deletable), "upload" (a game copy kept for an import, deletable when no import needs it)."""
    workspace = Path(net.workspace)
    roots = {Path(r["root"]).resolve(): r for r in net.store.all("SELECT slug, name, root FROM projects")}
    owned = [Path(d["parent"]).resolve() for d in imports.pending() if d.get("parent")]
    kept = {Path(d["source"]).parent.resolve() for d in imports.pending() if d.get("uploaded") and d.get("source")}
    rows: list[dict] = []

    def holds_project(folder: Path) -> bool:
        return any(r == folder or r.is_relative_to(folder) for r in roots)

    def row(child: Path, kind: str, **more) -> None:
        rows.append({"path": str(child), "kind": kind, **more})

    def walk(folder: Path, depth: int) -> None:
        try:
            children = sorted(p for p in folder.iterdir() if p.is_dir() and not p.is_symlink() and p.name != APP_DIR)
        except OSError:
            return
        for child in children:
            real = child.resolve()
            if real in roots:
                row(child, "project", slug=roots[real]["slug"], title=roots[real]["name"] or roots[real]["slug"])
            elif real in owned or any(o.is_relative_to(real) for o in owned):
                row(child, "import")
            elif holds_project(real) and depth < 4:  # a folder import's batch folder and its console folders
                walk(child, depth + 1)
            else:
                size, exact = folder_size(child)
                row(child, "leftover", bytes=size, exact=exact)

    if workspace.is_dir():
        walk(workspace, 0)
        uploads = workspace / APP_DIR / "uploads"
        for child in sorted(uploads.iterdir()) if uploads.is_dir() else ():
            if child.is_dir() and child.resolve() not in kept:
                size, exact = folder_size(child)
                row(child, "upload", bytes=size, exact=exact)
    # projects outside the projects folder (a saved project opened elsewhere) are still listed
    listed = {r["path"] for r in rows}
    for real, row in roots.items():
        if str(real) not in listed and not any(Path(p).resolve() == real for p in listed):
            rows.append({"path": str(real), "kind": "project", "slug": row["slug"], "title": row["name"] or row["slug"],
                         "outside": not _inside(real, workspace)})
    return {"root": str(workspace), "rows": rows}


def deletable(net, imports, path: Path) -> Path:
    """The folder to delete, or an error saying why not (ValueError: shown to the user)."""
    workspace = Path(net.workspace).resolve()
    target = path.resolve()
    if not target.is_dir() or target == workspace or not target.is_relative_to(workspace):
        raise ValueError("That folder isn't one decompdecomp keeps projects in.")
    data = workspace / APP_DIR
    if target == data or data.is_relative_to(target) or (
            target.is_relative_to(data) and target.parent != data / "uploads"):
        raise ValueError("That is decompdecomp's own data.")  # (only a kept upload, inside it, may go)
    for row in net.store.all("SELECT slug, root FROM projects"):
        root = Path(row["root"]).resolve()
        if root == target or root.is_relative_to(target) or target.is_relative_to(root):
            raise ValueError(f"The project {row['slug']} uses that folder: remove the project first.")
    for d in imports.pending():
        for owner in (d.get("parent"), str(Path(d["source"]).parent) if d.get("uploaded") and d.get("source") else None):
            if owner and (Path(owner).resolve() == target or Path(owner).resolve().is_relative_to(target)):
                raise ValueError("An import uses that folder: cancel the import first.")
    return target
