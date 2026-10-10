"""Archive paths preserve every original directory and reject traversal."""

import os

from .taxonomy import category_to_dir


def archive_path(save_root, file_path, analysis):
    if analysis.get("status") != "complete":
        return None
    category_dir = category_to_dir(
        analysis.get("category"),
        analysis.get("resource_kind"),
        analysis.get("subtype", ""),
    )
    if not category_dir:
        return None
    root = os.path.abspath(save_root)
    current = os.path.abspath(file_path)
    try:
        if os.path.commonpath([root, current]) != root or current == root:
            return None
        relative = os.path.relpath(current, root)
    except ValueError:
        return None
    prefix = os.path.join(*category_dir.split("/"))
    if relative.startswith(prefix + os.sep):
        return current
    return os.path.join(root, prefix, relative)
