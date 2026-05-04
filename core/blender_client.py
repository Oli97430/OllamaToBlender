"""TCP client for the blender-mcp-addon (server on port 9876)."""
from __future__ import annotations

import json
import socket
import textwrap
from dataclasses import dataclass
from typing import Any


@dataclass
class BlenderResult:
    status: str  # "ok" | "error" | "transport_error"
    result: Any = None
    stdout: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


# A preamble that the client prepends to every script. It finds a VIEW_3D area /
# region and wraps the entire user code in a `temp_override` so calls like
# `bpy.ops.object.select_all`, `mode_set`, `transform.*`, … succeed without
# requiring the model to remember the override boilerplate.
#
# Falls back to a null context when no 3D Viewport is available (headless /
# background mode), so the unwrapped behaviour is preserved in that case.
_V3D_PREAMBLE = """\
import bpy as _otb_bpy
import contextlib as _otb_ctx

def _otb_view3d_override():
    try:
        _scene = _otb_bpy.context.scene
        _vl = _otb_bpy.context.view_layer
        for _w in _otb_bpy.context.window_manager.windows:
            _scr = _w.screen
            for _a in _scr.areas:
                if _a.type == 'VIEW_3D':
                    _rgn = next((_r for _r in _a.regions if _r.type == 'WINDOW'), None)
                    if _rgn is None:
                        continue
                    _kw = {
                        'window': _w, 'screen': _scr, 'area': _a, 'region': _rgn,
                    }
                    if _scene is not None:
                        _kw['scene'] = _scene
                    if _vl is not None:
                        _kw['view_layer'] = _vl
                    return _otb_bpy.context.temp_override(**_kw)
    except Exception:
        pass
    return _otb_ctx.nullcontext()

# Best-effort: drop out of edit-mode so operators with a `mode=='OBJECT'` poll()
# (select_all, delete, transform.*, …) don't trip on stale state.
try:
    _otb_active = _otb_bpy.context.view_layer.objects.active if _otb_bpy.context.view_layer else None
    if _otb_active is not None and getattr(_otb_active, 'mode', 'OBJECT') != 'OBJECT':
        with _otb_view3d_override():
            _otb_bpy.ops.object.mode_set(mode='OBJECT')
except Exception:
    pass

with _otb_view3d_override():
"""


def wrap_with_view3d_override(code: str) -> str:
    """Wrap `code` so it executes inside a VIEW_3D context-override block.

    Variables defined inside a `with` statement live in the enclosing scope,
    so the addon still finds the user's top-level `result = ...`.
    """
    body = textwrap.indent(code.rstrip() + "\n", "    ")
    return _V3D_PREAMBLE + body


# Postamble that renders the active viewport to a PNG and stashes it into
# `result["_otb_render"]` as a base64 string. Used opt-in.
_RENDER_POSTAMBLE = """
# --- OllamaToBlender: viewport preview render -------------------------------
try:
    import base64 as _otb_b64
    import os as _otb_os
    import tempfile as _otb_tmp
    _otb_path = _otb_os.path.join(_otb_tmp.gettempdir(), '_otb_preview.png')
    _otb_scene = _otb_bpy.context.scene
    _otb_prev_path = _otb_scene.render.filepath
    _otb_prev_fmt = _otb_scene.render.image_settings.file_format
    _otb_prev_x = _otb_scene.render.resolution_x
    _otb_prev_y = _otb_scene.render.resolution_y
    _otb_scene.render.filepath = _otb_path
    _otb_scene.render.image_settings.file_format = 'PNG'
    _otb_scene.render.resolution_x = 720
    _otb_scene.render.resolution_y = 480
    with _otb_view3d_override():
        _otb_bpy.ops.render.opengl(write_still=True, view_context=True)
    _otb_scene.render.filepath = _otb_prev_path
    _otb_scene.render.image_settings.file_format = _otb_prev_fmt
    _otb_scene.render.resolution_x = _otb_prev_x
    _otb_scene.render.resolution_y = _otb_prev_y
    with open(_otb_path, 'rb') as _otb_f:
        _otb_png_b64 = _otb_b64.b64encode(_otb_f.read()).decode('ascii')
    if not isinstance(result, dict):
        result = {'_otb_user_result': result}
    result['_otb_render'] = _otb_png_b64
except Exception as _otb_exc:
    pass
"""


def wrap_with_render(code: str) -> str:
    """Wrap user code with VIEW_3D override AND a viewport-render postamble."""
    body = textwrap.indent(code.rstrip() + "\n", "    ")
    return _V3D_PREAMBLE + body + textwrap.indent(_RENDER_POSTAMBLE, "    ")


class BlenderClient:
    """Sends Python code to the Blender addon and reads the JSON response."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9876, timeout: float = 30.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def ping(self) -> bool:
        """Cheap connectivity check — runs `result = 'pong'` on the addon."""
        r = self.execute("result = 'pong'", timeout=2.0)
        return r.ok and r.result == "pong"

    def execute(
        self,
        code: str,
        timeout: float | None = None,
        *,
        auto_v3d: bool = True,
        with_render: bool = False,
    ) -> BlenderResult:
        if with_render:
            code = wrap_with_render(code)
        elif auto_v3d:
            code = wrap_with_view3d_override(code)
        payload = json.dumps({"type": "execute", "code": code, "strict_json": False})
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout or self.timeout) as sock:
                sock.settimeout(timeout or self.timeout)
                sock.sendall(payload.encode("utf-8") + b"\x00")
                buf = bytearray()
                while True:
                    chunk = sock.recv(8192)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    if b"\x00" in chunk:
                        break
            raw = bytes(buf).rstrip(b"\x00").decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            return BlenderResult(
                status=data.get("status", "error"),
                result=data.get("result"),
                stdout=data.get("stdout", ""),
                message=data.get("message", ""),
            )
        except (ConnectionRefusedError, socket.timeout, OSError) as exc:
            return BlenderResult(status="transport_error", message=f"{type(exc).__name__}: {exc}")
        except json.JSONDecodeError as exc:
            return BlenderResult(status="transport_error", message=f"Invalid JSON from Blender: {exc}")
