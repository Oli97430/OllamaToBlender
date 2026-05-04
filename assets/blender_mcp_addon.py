# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Olivier Hoarau <Tarraw974@gmail.com>
"""
Blender MCP Server Addon
========================
Exposes a local TCP server (port 9876) so that Claude / Cowork can send
Python code to execute inside Blender and receive the results in real-time.

Protocol
--------
- Transport : raw TCP, null-byte (\\0) delimited JSON frames
- Request   : {"type": "execute", "code": "<python>", "strict_json": false}
- Response  : {"status": "ok"|"error", "result": ..., "stdout": "...",
               "message": "<traceback on error>"}

Architecture
------------
- A background accept-thread waits for incoming connections.
- Each connection is handled in its own daemon thread: it reads the request,
  pushes a task onto a thread-safe queue, then polls for the result.
- A Blender timer (_tick, every 50 ms) drains the queue on the **main thread**,
  executes the code via exec(), and stores the result.
- The server state is kept in sys.modules[_MOD] so it survives exec() scoping
  and Blender's operator system without relying on closures or globals.
"""

bl_info = {
    "name":        "MCP Server",
    "description": (
        "Starts a local TCP server (port 9876) so that Claude / Cowork "
        "can execute Python code inside Blender."
    ),
    "author":      "Olivier Hoarau <Tarraw974@gmail.com>",
    "version":     (1, 3, 0),
    "blender":     (4, 0, 0),
    "location":    "3D Viewport > N-Panel > MCP",
    "doc_url":     "https://github.com/Tarraw974/blender-mcp-addon",
    "tracker_url": "https://github.com/Tarraw974/blender-mcp-addon/issues",
    "category":    "Interface",
}

import bpy
import gc
import json
import queue
import socket
import sys
import threading
import traceback
import types
from io import StringIO

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MOD         = "_blender_mcp_srv"      # key in sys.modules for persistent state
_DEFAULT_PORT = 9876
_HOST         = "localhost"
_TICK_INTERVAL = 0.05                  # seconds between main-thread ticks
_CLIENT_TIMEOUT = 300.0               # seconds before a client connection times out
_EXEC_TIMEOUT   = 30.0                # seconds to wait for code execution


# ---------------------------------------------------------------------------
# Persistent state (lives in sys.modules so it survives exec() scoping)
# ---------------------------------------------------------------------------

class _State:
    """All mutable server state, stored in sys.modules[_MOD].st."""
    sock:    socket.socket | None = None
    tq:      queue.Queue  | None = None   # task queue  (tid, code)
    rm:      dict         | None = None   # result map  {tid: response}
    rl:      threading.Lock | None = None # lock for rm and ctr
    ctr:     list         | None = None   # [int] monotone task id counter
    running: bool                = False
    port:    int                 = _DEFAULT_PORT


def _get_state() -> _State:
    """Return the persistent _State, creating it if necessary."""
    if _MOD not in sys.modules:
        m = types.ModuleType(_MOD)
        m.st         = _State()
        m.st.tq      = queue.Queue()
        m.st.rm      = {}
        m.st.rl      = threading.Lock()
        m.st.ctr     = [0]
        sys.modules[_MOD] = m
    return sys.modules[_MOD].st


# ---------------------------------------------------------------------------
# Main-thread timer — executes queued code inside Blender
# ---------------------------------------------------------------------------

def _tick() -> float | None:
    """
    Called every _TICK_INTERVAL seconds on Blender's main thread.
    Drains the task queue, exec()s each piece of code, stores the result.
    Returns None to unregister itself when the server is stopped.
    """
    m = sys.modules.get(_MOD)
    if m is None or not m.st.running:
        return None  # unregister timer

    s = m.st
    try:
        while not s.tq.empty():
            tid, code = s.tq.get_nowait()

            # Capture stdout / stderr produced by the user code
            saved_out, saved_err = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = cap = StringIO()
            try:
                ns = {"bpy": bpy, "result": None}
                exec(compile(code, "<mcp>", "exec"), ns)
                res = {
                    "status": "ok",
                    "result": ns.get("result"),
                    "stdout": cap.getvalue(),
                }
            except Exception:
                res = {
                    "status": "error",
                    "message": traceback.format_exc(),
                    "stdout": cap.getvalue(),
                }
            finally:
                sys.stdout, sys.stderr = saved_out, saved_err

            with s.rl:
                s.rm[tid] = res

    except Exception:
        pass  # never crash the timer

    return _TICK_INTERVAL


# ---------------------------------------------------------------------------
# Per-connection handler (background thread)
# ---------------------------------------------------------------------------

def _handle(conn: socket.socket) -> None:
    """
    Reads one null-delimited JSON request, dispatches it to the main thread
    via the task queue, waits for the result, sends the response.
    """
    import time

    m = sys.modules.get(_MOD)
    if m is None:
        conn.close()
        return

    s = m.st
    try:
        # --- read request ---
        buf = bytearray()
        conn.settimeout(_CLIENT_TIMEOUT)
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf.extend(chunk)
            if b"\0" in buf:
                break

        if not buf:
            return

        raw = buf.split(b"\0")[0]
        req = json.loads(raw.decode("utf-8"))
        code = req.get("code", "")

        # --- enqueue ---
        with s.rl:
            s.ctr[0] += 1
            tid = s.ctr[0]
        s.tq.put((tid, code))

        # --- wait for result ---
        deadline = time.monotonic() + _EXEC_TIMEOUT
        while time.monotonic() < deadline:
            with s.rl:
                if tid in s.rm:
                    response = s.rm.pop(tid)
                    conn.sendall(
                        (json.dumps(response, default=str) + "\0").encode("utf-8")
                    )
                    return
            time.sleep(_TICK_INTERVAL)

        # Timed out
        conn.sendall(
            (json.dumps({"status": "error",
                         "message": f"Execution timed out after {_EXEC_TIMEOUT:.0f}s"})
             + "\0").encode("utf-8")
        )

    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        try:
            conn.sendall(
                (json.dumps({"status": "error",
                             "message": f"Protocol error: {exc}"}) + "\0").encode("utf-8")
            )
        except Exception:
            pass
    except Exception as exc:
        try:
            conn.sendall(
                (json.dumps({"status": "error",
                             "message": str(exc)}) + "\0").encode("utf-8")
            )
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Accept loop (background thread)
# ---------------------------------------------------------------------------

def _accept_loop(srv_sock: socket.socket) -> None:
    """Accepts incoming connections and spawns a handler thread for each."""
    while True:
        try:
            conn, _ = srv_sock.accept()
            threading.Thread(target=_handle, args=(conn,), daemon=True).start()
        except Exception:
            break  # socket closed → exit loop


# ---------------------------------------------------------------------------
# Stale-socket cleanup (Windows SO_REUSEADDR quirk)
# ---------------------------------------------------------------------------

def _close_stale_sockets(keep_fd: int, port: int) -> int:
    """
    On Windows, SO_REUSEADDR allows multiple sockets to bind the same port.
    Old server instances (from previous exec() calls) may still be listening
    and intercepting connections.  This function closes them all.

    Returns the number of sockets closed.
    """
    closed = 0
    for obj in list(gc.get_objects()):
        try:
            if not isinstance(obj, socket.socket):
                continue
            fd = obj.fileno()
            if fd <= 0 or fd == keep_fd:
                continue
            try:
                if obj.getsockname()[1] == port:
                    obj.close()
                    closed += 1
            except Exception:
                pass
        except Exception:
            pass
    return closed


# ---------------------------------------------------------------------------
# Public API: start / stop / status
# ---------------------------------------------------------------------------

def server_start(port: int | None = None) -> str:
    """
    Start the MCP TCP server.
    Returns a human-readable status string.
    """
    s = _get_state()
    if port is not None:
        s.port = port
    effective_port = s.port

    if s.running and s.sock:
        return f"Already running on port {effective_port}"

    try:
        s.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.sock.bind((_HOST, effective_port))
        s.sock.listen(5)

        # Must happen AFTER bind so we know our own fd
        stale = _close_stale_sockets(s.sock.fileno(), effective_port)
        if stale:
            print(f"[MCP] Closed {stale} stale socket(s) on port {effective_port}")

        threading.Thread(target=_accept_loop, args=(s.sock,), daemon=True).start()
        s.running = True

        if not bpy.app.timers.is_registered(_tick):
            bpy.app.timers.register(_tick, persistent=True)

        return f"MCP server ready on port {effective_port} ✓"

    except OSError as exc:
        if s.sock:
            try:
                s.sock.close()
            except Exception:
                pass
            s.sock = None
        return f"Failed to start: {exc}"


def server_stop() -> str:
    """Stop the MCP TCP server. Returns a status string."""
    m = sys.modules.get(_MOD)
    if m is None:
        return "Server not running"

    s = m.st
    s.running = False

    if s.sock:
        try:
            s.sock.close()
        except Exception:
            pass
        s.sock = None

    if bpy.app.timers.is_registered(_tick):
        try:
            bpy.app.timers.unregister(_tick)
        except Exception:
            pass

    return "MCP server stopped"


def server_restart() -> str:
    """Stop, purge state, and restart the server."""
    server_stop()
    if _MOD in sys.modules:
        del sys.modules[_MOD]
    return server_start()


def server_status() -> str:
    """Return a one-line status string."""
    m = sys.modules.get(_MOD)
    if m and m.st.running and m.st.sock:
        port = getattr(m.st, "port", _DEFAULT_PORT)
        return f"● Running  (port {port})"
    return "○ Stopped"


# ---------------------------------------------------------------------------
# Addon preferences (configurable port)
# ---------------------------------------------------------------------------

class MCPAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    port: bpy.props.IntProperty(  # type: ignore[valid-type]
        name="Port",
        description="TCP port the MCP server listens on",
        default=_DEFAULT_PORT,
        min=1024,
        max=65535,
    )
    auto_start: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="Auto-start on Blender launch",
        description="Automatically start the server when Blender opens",
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "port")
        layout.prop(self, "auto_start")


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class MCP_OT_start(bpy.types.Operator):
    bl_idname      = "mcp.start_server"
    bl_label       = "Start Server"
    bl_description = "Start the MCP TCP server on the configured port"

    def execute(self, context):
        prefs = context.preferences.addons.get(__name__)
        port  = prefs.preferences.port if prefs else _DEFAULT_PORT
        msg   = server_start(port)
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class MCP_OT_stop(bpy.types.Operator):
    bl_idname      = "mcp.stop_server"
    bl_label       = "Stop Server"
    bl_description = "Stop the MCP TCP server"

    def execute(self, context):
        msg = server_stop()
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class MCP_OT_restart(bpy.types.Operator):
    bl_idname      = "mcp.restart_server"
    bl_label       = "Restart"
    bl_description = "Restart the server (useful if the connection is stuck)"

    def execute(self, context):
        msg = server_restart()
        self.report({"INFO"}, msg)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# N-Panel
# ---------------------------------------------------------------------------

class MCP_PT_panel(bpy.types.Panel):
    bl_label       = "MCP Server"
    bl_idname      = "MCP_PT_panel"
    bl_space_type  = "VIEW_3D"
    bl_region_type = "UI"
    bl_category    = "MCP"

    def draw(self, context):
        layout  = self.layout
        status  = server_status()
        running = status.startswith("●")

        box = layout.box()
        box.label(
            text=status,
            icon="SEQUENCE_COLOR_04" if running else "SEQUENCE_COLOR_01",
        )

        col = layout.column(align=True)
        if running:
            col.operator("mcp.stop_server",    icon="PAUSE")
            col.operator("mcp.restart_server", icon="FILE_REFRESH")
        else:
            col.operator("mcp.start_server",   icon="PLAY")

        layout.separator()
        prefs = context.preferences.addons.get(__name__)
        port  = prefs.preferences.port if prefs else _DEFAULT_PORT
        layout.label(text=f"Port: {port}", icon="NETWORK_DRIVE")
        layout.label(text="Protocol: TCP / JSON+\\0", icon="SCRIPT")
        layout.operator(
            "preferences.addon_show",
            text="Preferences",
            icon="PREFERENCES",
        ).module = __name__


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = [
    MCPAddonPreferences,
    MCP_OT_start,
    MCP_OT_stop,
    MCP_OT_restart,
    MCP_PT_panel,
]


def _auto_start() -> None:
    """Deferred auto-start, called once 0.5 s after Blender initialises."""
    prefs = bpy.context.preferences.addons.get(__name__)
    if prefs and not prefs.preferences.auto_start:
        return None
    port = prefs.preferences.port if prefs else _DEFAULT_PORT
    msg = server_start(port)
    print(f"[MCP Addon] {msg}")
    return None  # do not repeat


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.app.timers.register(_auto_start, first_interval=0.5)
    print("[MCP Addon] Registered — server will auto-start in 0.5 s")


def unregister() -> None:
    server_stop()
    if _MOD in sys.modules:
        del sys.modules[_MOD]
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    print("[MCP Addon] Unregistered")


if __name__ == "__main__":
    register()
