"""paperless-view DATABASE DOCUMENTS VIEW: each Paperless document as a file.

DOCUMENTS (Paperless's media/documents) is no clean picture of the library: it
keeps the documents in the trash until the trash is emptied, the older files
of documents that have versions, and splits each document into originals/
(the file as it came in) and archive/ (the PDF with Paperless's OCR text).
VIEW gets one file per document the web UI lists, the same file its Download
button serves: the newest version, its archive PDF if it has one and the
original otherwise, named like Paperless's storage (PAPERLESS_FILENAME_FORMAT:
<year>/<correspondent>/<title>.pdf).

The files are hardlinks, so they take no space and keep Paperless's owner and
permissions; VIEW must be on the file system of DOCUMENTS. DATABASE (SQLite)
is only read. Each run brings VIEW up to date and prints a line if anything
changed; a file Paperless renames or deletes during a run is picked up by the
next one.
"""

import os
import sqlite3
import sys
from pathlib import Path

# In the directory of the file it becomes, so the swap is a rename.
TEMPORARY = ".paperless-view.tmp"


def documents(database, storage):
    """Map each path in the view to the stored file it shows."""
    connection = sqlite3.connect(
        f"file:{database}?mode=ro", uri=True, timeout=60
    )
    try:
        rows = connection.execute(
            "SELECT id, root_document_id, version_index, filename,"
            " archive_filename FROM documents_document"
            " WHERE deleted_at IS NULL"
        ).fetchall()
    finally:
        connection.close()

    # A document is a row without root_document_id, its versions are the rows
    # pointing at it. The newest version wins the way Paperless picks it
    # (documents/versioning.py): highest version_index, no index last, then
    # highest id; the document's own row only counts when it has no versions.
    files = {}  # document -> (file, archive file) of its own row
    versions = {}  # document -> (rank, file, archive file) of the newest
    for row, root, index, original, archive in rows:
        if root is None:
            files[row] = (original, archive)
            continue
        rank = (index is not None, index or 0, row)
        if root not in versions or rank > versions[root][0]:
            versions[root] = (rank, original, archive)

    tree = {}
    # In id order, so a name clash always resolves the same way. Versions of a
    # document in the trash are not in `files`, so they are left out.
    for document in sorted(files):
        name = files[document][0]
        if document in versions:
            original, archive = versions[document][1:]
        else:
            original, archive = files[document]
        if not name:
            continue
        if archive:
            source = storage / "archive" / archive
        elif original:
            source = storage / "originals" / original
        else:
            continue
        # An image's archive PDF can take the name of another document's PDF.
        path = Path(name).with_suffix(source.suffix)
        if path in tree:
            path = path.with_name(f"{path.stem} ({document}){path.suffix}")
        tree[path] = source
    return tree


def sync(view, tree):
    """Make VIEW hold exactly TREE: link what is new or changed, then prune."""
    linked = removed = 0
    failed = []
    for path, source in tree.items():
        target = view / path
        try:
            stored = source.stat()
        except FileNotFoundError:
            continue  # renamed or deleted since the query
        try:
            if os.path.samestat(target.lstat(), stored):
                continue
        except FileNotFoundError:
            pass
        temporary = target.parent / TEMPORARY
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary.unlink(missing_ok=True)
            os.link(source, temporary)
            # One step, so a reader never finds the file missing.
            os.replace(temporary, target)
        except FileNotFoundError:
            continue  # renamed or deleted since the stat
        except OSError as error:
            failed.append(f"{path}: {error}")
            continue
        linked += 1

    for directory, _, names in os.walk(view, topdown=False):
        directory = Path(directory)
        for name in names:
            path = directory / name
            if path.relative_to(view) not in tree:
                path.unlink()
                removed += 1
        if directory != view and not any(directory.iterdir()):
            directory.rmdir()
    return linked, removed, failed


def main():
    database, storage, view = (Path(argument) for argument in sys.argv[1:])
    tree = documents(database, storage)
    linked, removed, failed = sync(view, tree)
    if linked or removed:
        summary = f"{len(tree)} documents: {linked} linked, {removed} removed"
        print(summary, flush=True)
    for line in failed:
        print(f"cannot link {line}", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
