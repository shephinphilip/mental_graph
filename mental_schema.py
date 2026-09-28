from pathlib import Path
import json

# ============================================================
# CONFIGURATION
# ============================================================

# Use the current folder as the project root.
# You can replace Path.cwd() with Path(r"C:\company\chat\mental_health")
PROJECT_ROOT = Path.cwd()

# Folders/files to ignore
IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "node_modules",
    ".cursor",
}

IGNORE_FILES = {
    ".DS_Store",
}

# Output files
TREE_OUTPUT = PROJECT_ROOT / "project_structure.txt"
JSON_OUTPUT = PROJECT_ROOT / "project_structure.json"


# ============================================================
# TREE GENERATOR
# ============================================================

def build_tree(path: Path, prefix: str = ""):
    """
    Recursively build a readable project tree.
    Returns a list of strings.
    """

    lines = []

    try:
        entries = list(path.iterdir())
    except PermissionError:
        lines.append(f"{prefix}└── [Permission Denied]")
        return lines

    # Filter ignored items
    entries = [
        entry
        for entry in entries
        if not (
            entry.is_dir() and entry.name in IGNORE_DIRS
        )
        and not (
            entry.is_file() and entry.name in IGNORE_FILES
        )
    ]

    # Sort: folders first, then files
    entries.sort(key=lambda x: (not x.is_dir(), x.name.lower()))

    for index, entry in enumerate(entries):
        is_last = index == len(entries) - 1

        connector = "└── " if is_last else "├── "
        next_prefix = prefix + ("    " if is_last else "│   ")

        if entry.is_dir():
            lines.append(f"{prefix}{connector}{entry.name}/")
            lines.extend(build_tree(entry, next_prefix))

        else:
            lines.append(f"{prefix}{connector}{entry.name}")

    return lines


# ============================================================
# JSON STRUCTURE
# ============================================================

def build_json_tree(path: Path):
    """
    Build a machine-readable JSON representation.
    """

    result = {
        "name": path.name,
        "type": "directory",
        "children": []
    }

    try:
        entries = list(path.iterdir())
    except PermissionError:
        result["error"] = "Permission denied"
        return result

    entries = [
        entry
        for entry in entries
        if not (
            entry.is_dir() and entry.name in IGNORE_DIRS
        )
        and not (
            entry.is_file() and entry.name in IGNORE_FILES
        )
    ]

    entries.sort(key=lambda x: (not x.is_dir(), x.name.lower()))

    for entry in entries:
        if entry.is_dir():
            result["children"].append(build_json_tree(entry))
        else:
            try:
                size = entry.stat().st_size
            except OSError:
                size = None

            result["children"].append({
                "name": entry.name,
                "type": "file",
                "extension": entry.suffix,
                "size_bytes": size
            })

    return result


# ============================================================
# MAIN
# ============================================================

def main():
    print(f"\nProject root:\n{PROJECT_ROOT}\n")

    # -------------------------
    # Generate text tree
    # -------------------------
    tree_lines = [
        PROJECT_ROOT.name + "/"
    ]

    tree_lines.extend(build_tree(PROJECT_ROOT, ""))

    tree_text = "\n".join(tree_lines)

    TREE_OUTPUT.write_text(
        tree_text,
        encoding="utf-8"
    )

    # -------------------------
    # Generate JSON tree
    # -------------------------
    json_tree = build_json_tree(PROJECT_ROOT)

    JSON_OUTPUT.write_text(
        json.dumps(json_tree, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    # -------------------------
    # Print to terminal
    # -------------------------
    print(tree_text)

    print("\n" + "=" * 70)
    print(f"Saved tree : {TREE_OUTPUT}")
    print(f"Saved JSON : {JSON_OUTPUT}")
    print("=" * 70)


if __name__ == "__main__":
    main()