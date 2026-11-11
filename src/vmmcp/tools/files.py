"""tools/files.py — File system."""

from __future__ import annotations

import base64
import hashlib
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from ..mcp_instance import mcp
from ..winutil import run_ps


@mcp.tool()
def file_read(path: str, encoding: str = "utf-8", as_base64: bool = False) -> dict:
    """
    Read a file from the VM.

    Args:
        path: File path.
        encoding: Text encoding (default utf-8). Ignored if as_base64=True.
        as_base64: Return raw bytes as base64 (for binaries/images).

    Returns: {content, size_bytes, path}
    """
    p = Path(path)
    if not p.exists():
        return {"error": f"Not found: {path}"}
    size = p.stat().st_size
    content = (
        base64.b64encode(p.read_bytes()).decode("ascii")
        if as_base64
        else p.read_text(encoding=encoding, errors="replace")
    )
    return {"content": content, "size_bytes": size, "path": str(p.resolve())}


@mcp.tool()
def file_write(
    path: str,
    content: str,
    encoding: str = "utf-8",
    from_base64: bool = False,
    create_dirs: bool = True,
) -> dict:
    """
    Write / overwrite a file on the VM.

    Args:
        path: Destination path.
        content: Text content, or base64 bytes if from_base64=True.
        encoding: Text encoding (default utf-8).
        from_base64: Decode content from base64 before writing.
        create_dirs: Create parent directories if needed.

    Returns: {path, size_bytes}
    """
    p = Path(path)
    if create_dirs:
        p.parent.mkdir(parents=True, exist_ok=True)
    if from_base64:
        p.write_bytes(base64.b64decode(content))
    else:
        p.write_text(content, encoding=encoding)
    return {"path": str(p.resolve()), "size_bytes": p.stat().st_size}


@mcp.tool()
def file_delete(path: str, recursive: bool = False) -> dict:
    """Delete a file or directory."""
    p = Path(path)
    if not p.exists():
        return {"error": f"Not found: {path}"}
    if p.is_dir():
        if not recursive:
            return {"error": "Is a directory — set recursive=True"}
        shutil.rmtree(p)
    else:
        p.unlink()
    return {"deleted": True, "path": str(p)}


@mcp.tool()
def file_list(
    path: str = ".",
    pattern: str = "*",
    recursive: bool = False,
    include_hidden: bool = False,
) -> dict:
    """
    List directory contents.

    Args:
        path: Directory path (default current dir).
        pattern: Glob pattern (default *).
        recursive: Recurse subdirectories.
        include_hidden: Include dotfiles.

    Returns: {entries, path, count}
    """
    p = Path(path)
    if not p.exists():
        return {"error": f"Not found: {path}"}
    entries = []
    fn = p.rglob if recursive else p.glob
    for item in sorted(fn(pattern)):
        if not include_hidden and item.name.startswith("."):
            continue
        try:
            st = item.stat()
            entries.append({
                "name":       str(item.relative_to(p)) if recursive else item.name,
                "type":       "dir" if item.is_dir() else "file",
                "size_bytes": st.st_size if item.is_file() else None,
                "modified":   datetime.fromtimestamp(st.st_mtime).isoformat(),
            })
        except (PermissionError, OSError):
            pass
    return {"entries": entries, "path": str(p.resolve()), "count": len(entries)}


@mcp.tool()
def file_copy(source: str, destination: str, overwrite: bool = True) -> dict:
    """
    Copy a file or directory tree.

    Args:
        source: Source path.
        destination: Destination path.
        overwrite: Overwrite existing destination (default True).

    Returns: {copied, destination}
    """
    src = Path(source)
    dst = Path(destination)
    if not src.exists():
        return {"error": f"Source not found: {source}"}
    if src.is_dir():
        if dst.exists() and overwrite:
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return {"copied": True, "destination": str(dst.resolve())}


@mcp.tool()
def file_move(source: str, destination: str) -> dict:
    """Move or rename a file or directory."""
    src = Path(source)
    dst = Path(destination)
    if not src.exists():
        return {"error": f"Source not found: {source}"}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), dst)
    return {"moved": True, "source": str(src), "destination": str(dst.resolve())}


@mcp.tool()
def file_hash(path: str, algorithm: str = "sha256") -> dict:
    """
    Compute a cryptographic hash of a file.

    Args:
        path: File path.
        algorithm: Hash algorithm — sha256 (default), md5, sha1.

    Returns: {hash, algorithm, path, size_bytes}
    """
    p = Path(path)
    if not p.exists():
        return {"error": f"Not found: {path}"}
    algo = algorithm.lower()
    h = hashlib.new(algo)
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"hash": h.hexdigest(), "algorithm": algo, "path": str(p), "size_bytes": p.stat().st_size}


@mcp.tool()
def archive_create(
    output_path: str,
    sources: list[str],
    format: str = "zip",
) -> dict:
    """
    Create a zip archive from files or directories.

    Args:
        output_path: Destination archive path (e.g. C:\\Temp\\out.zip).
        sources: List of file/directory paths to include.
        format: Archive format — zip (default) or tar.

    Returns: {path, size_bytes, files_added}
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    files_added = 0
    if format == "zip":
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for src in sources:
                s = Path(src)
                if s.is_dir():
                    for f in s.rglob("*"):
                        if f.is_file():
                            zf.write(f, f.relative_to(s.parent))
                            files_added += 1
                elif s.exists():
                    zf.write(s, s.name)
                    files_added += 1
    elif format == "tar":
        import tarfile
        with tarfile.open(out, "w:gz") as tf:
            for src in sources:
                tf.add(src, arcname=Path(src).name)
                files_added += 1
    else:
        return {"error": f"Unsupported format: {format}. Use zip or tar."}
    return {"path": str(out.resolve()), "size_bytes": out.stat().st_size, "files_added": files_added}


@mcp.tool()
def archive_extract(
    archive_path: str,
    destination: str,
    overwrite: bool = True,
) -> dict:
    """
    Extract a zip, tar.gz, or 7z archive.

    Args:
        archive_path: Path to the archive.
        destination: Directory to extract into.
        overwrite: Overwrite existing files (default True).

    Returns: {extracted_to, files_extracted}
    """
    arc = Path(archive_path)
    dst = Path(destination)
    dst.mkdir(parents=True, exist_ok=True)
    if not arc.exists():
        return {"error": f"Archive not found: {archive_path}"}
    name = arc.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(arc) as zf:
            count = len(zf.namelist())
            zf.extractall(dst)
    elif name.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar")):
        import tarfile
        with tarfile.open(arc) as tf:
            count = len(tf.getmembers())
            tf.extractall(dst)
    elif name.endswith(".7z"):
        # Try via PowerShell 7-zip if installed
        r = run_ps(f'7z x "{arc}" -o"{dst}" -y')
        return {"extracted_to": str(dst), "exit_code": r["exit_code"], "stderr": r["stderr"]}
    else:
        # Generic fallback: Expand-Archive (zip only)
        r = run_ps(f'Expand-Archive -Path "{arc}" -DestinationPath "{dst}" -Force')
        return {"extracted_to": str(dst), "exit_code": r["exit_code"]}
    return {"extracted_to": str(dst.resolve()), "files_extracted": count}
