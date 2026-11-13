"""activity.py — in-memory + on-disk record of everything the AI does (feeds the web dashboard).

Event kinds
  tool        one MCP tool call:  tool, args, status (running|ok|error), dur_ms, summary, images[]
  connection  an MCP client connected / disconnected / was rate-limited / failed auth
  system      server start, agent start/stop, ...

Persistence: events.jsonl (+ img/<id>.<ext>) under ACTIVITY_DIR, pruned on start. Disk problems never break tools.
"""

from __future__ import annotations

import collections
import io
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .config import ACTIVITY_DIR

MAX_EVENTS = 1500
MAX_DISK_IMAGES = 150
_SECRET = re.compile(r"pass|secret|token|api.?key|credential", re.I)


def _trunc(v: Any, n: int = 400) -> Any:
    if isinstance(v, str):
        return v if len(v) <= n else v[:n] + f"… (+{len(v) - n} chars)"
    if isinstance(v, (list, tuple)):
        out = [_trunc(x, n) for x in list(v)[:20]]
        return out + [f"… (+{len(v) - 20} more)"] if len(v) > 20 else out
    if isinstance(v, dict):
        return {str(k): ("***" if _SECRET.search(str(k)) else _trunc(x, n)) for k, x in list(v.items())[:40]}
    return v


def _is_image(x: Any) -> bool:
    return isinstance(getattr(x, "data", None), (bytes, bytearray))


def summarize(result: Any) -> tuple[str, list[tuple[bytes, str]]]:
    """-> (short text, [(image_bytes, ext)])"""
    texts: list[str] = []
    images: list[tuple[bytes, str]] = []
    items = result if isinstance(result, (list, tuple)) else [result]
    for it in items:
        if _is_image(it):
            fmt = str(getattr(it, "format", None) or getattr(it, "_format", None) or "png").lower().replace("jpg", "jpeg")
            images.append((bytes(it.data), "jpg" if fmt == "jpeg" else fmt))
        elif isinstance(it, str):
            texts.append(it)
        elif isinstance(it, (dict, list)):
            texts.append(json.dumps(_trunc(it, 300), default=str, ensure_ascii=False))
        elif it is not None:
            texts.append(str(it))
    text = " | ".join(texts)
    return (text if len(text) <= 900 else text[:900] + "…"), images


class Store:
    def __init__(self, directory: Optional[Path] = None):
        self.lock = threading.RLock()
        self.events: list[dict] = []
        self.by_id: dict[int, dict] = {}
        self.seq = 0
        self.started = time.time()
        self.active_connections = 0
        self.dir = Path(directory) if directory else None
        self._mem_images: collections.OrderedDict[int, tuple[bytes, str]] = collections.OrderedDict()
        self._load()

    # ── persistence ──────────────────────────────────────────────────────────
    def _load(self) -> None:
        if not self.dir:
            return
        try:
            (self.dir / "img").mkdir(parents=True, exist_ok=True)
            path = self.dir / "events.jsonl"
            if path.exists():
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-MAX_EVENTS:]:
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if ev.get("status") == "running":
                        ev["status"], ev["error"] = "error", "server restarted before this call finished"
                    self.by_id[ev["id"]] = ev
                self.events = sorted(self.by_id.values(), key=lambda e: e["id"])
                self.seq = self.events[-1]["id"] if self.events else 0
                path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self.events), encoding="utf-8")
            imgs = sorted((self.dir / "img").glob("*"), key=lambda p: p.stat().st_mtime)
            for p in imgs[:-MAX_DISK_IMAGES]:
                p.unlink(missing_ok=True)
        except OSError:
            self.dir = None

    def _persist(self, ev: dict) -> None:
        if not self.dir:
            return
        try:
            with open(self.dir / "events.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ── recording ────────────────────────────────────────────────────────────
    def _new(self, kind: str, **fields) -> dict:
        with self.lock:
            self.seq += 1
            ev = {"id": self.seq, "ts": time.time(), "kind": kind, **fields}
            self.events.append(ev)
            self.by_id[ev["id"]] = ev
            if len(self.events) > MAX_EVENTS:
                old = self.events.pop(0)
                self.by_id.pop(old["id"], None)
            return ev

    def begin(self, tool: str, args: dict) -> int:
        return self._new("tool", tool=tool, args=_trunc(args), status="running", images=[])["id"]

    def end(self, event_id: int, ok: bool, result: Any = None, error: str = "") -> None:
        ev = self.by_id.get(event_id)
        if not ev:
            return
        summary, imgs = summarize(result) if ok else ("", [])
        refs = [self.add_image(b, ext, ev["tool"]) for b, ext in imgs]
        with self.lock:
            ev.update(status="ok" if ok else "error", dur_ms=int((time.time() - ev["ts"]) * 1000),
                      summary=summary, images=refs, error=error)
        self._persist(ev)

    def note(self, kind: str, text: str, **extra) -> dict:
        ev = self._new(kind, text=text, status=extra.pop("status", "ok"), **extra)
        self._persist(ev)
        return ev

    def add_image(self, data: bytes, ext: str, source: str = "") -> dict:
        with self.lock:
            self.seq += 1
            img_id = self.seq
        w = h = 0
        try:
            from PIL import Image
            w, h = Image.open(io.BytesIO(data)).size
        except Exception:
            pass
        self._mem_images[img_id] = (data, ext)
        while len(self._mem_images) > 30:
            self._mem_images.popitem(last=False)
        if self.dir:
            try:
                (self.dir / "img" / f"{img_id}.{ext}").write_bytes(data)
            except OSError:
                pass
        return {"id": img_id, "w": w, "h": h, "ext": ext, "bytes": len(data)}

    def image(self, img_id: int) -> Optional[tuple[bytes, str]]:
        if img_id in self._mem_images:
            return self._mem_images[img_id]
        if self.dir:
            for p in (self.dir / "img").glob(f"{img_id}.*"):
                try:
                    return p.read_bytes(), p.suffix.lstrip(".")
                except OSError:
                    return None
        return None

    # ── connections ──────────────────────────────────────────────────────────
    def connection_opened(self, client: str) -> int:
        with self.lock:
            self.active_connections += 1
        return self.note("connection", f"MCP client connected from {client}", event="open", client=client)["id"]

    def connection_closed(self, client: str, seconds: float, reason: str) -> None:
        with self.lock:
            self.active_connections = max(0, self.active_connections - 1)
        bad = reason not in ("client closed", "server shutdown")
        self.note("connection", f"MCP client {client} disconnected after {int(seconds)}s — {reason}",
                  event="close", client=client, seconds=round(seconds, 1), reason=reason,
                  status="error" if bad else "ok")

    # ── queries ──────────────────────────────────────────────────────────────
    def after(self, after_id: int = 0, limit: int = 300) -> list[dict]:
        with self.lock:
            out = [dict(e) for e in self.events if e["id"] > after_id]
        return out[-limit:]

    def updated_since(self, ids: list[int]) -> list[dict]:
        with self.lock:
            return [dict(self.by_id[i]) for i in ids if i in self.by_id]

    def stats(self) -> dict:
        with self.lock:
            tools = [e for e in self.events if e["kind"] == "tool"]
            return {"uptime_s": int(time.time() - self.started), "calls": len(tools),
                    "errors": sum(1 for e in tools if e["status"] == "error"),
                    "running": [{"id": e["id"], "tool": e["tool"], "ts": e["ts"]} for e in tools if e["status"] == "running"],
                    "connections": self.active_connections, "last_id": self.seq}


store = Store(ACTIVITY_DIR)
