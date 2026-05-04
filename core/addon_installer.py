"""Detect Blender install dirs and install / update the blender-mcp-addon."""
from __future__ import annotations

import platform
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests

ADDON_FILE_NAME = "blender_mcp_addon.py"
ADDON_REMOTE_URL = (
    "https://raw.githubusercontent.com/Oli97430/blender-mcp-addon/main/blender_mcp_addon.py"
)
BUNDLED_ADDON_PATH = Path(__file__).resolve().parent.parent / "assets" / ADDON_FILE_NAME

VERSION_RE = re.compile(r'"version"\s*:\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)')
NAME_RE = re.compile(r'"name"\s*:\s*"([^"]+)"')


# ---------------------------------------------------------------- discovery


@dataclass
class BlenderAddonDir:
    """A `<blender>/<version>/scripts/addons/` directory we can install into."""
    version: str          # "4.2", "4.5", "5.0", ...
    path: Path            # full path to the addons directory
    installed_version: str = ""   # tuple-style, e.g. "1.3.0", empty if absent

    @property
    def label(self) -> str:
        return f"Blender {self.version}  —  {self.path}"

    @property
    def is_installed(self) -> bool:
        return bool(self.installed_version)

    @property
    def addon_file(self) -> Path:
        return self.path / ADDON_FILE_NAME


def _candidate_roots() -> list[Path]:
    """Possible parents of the per-version Blender config dirs on this OS."""
    home = Path.home()
    system = platform.system()
    roots: list[Path] = []

    if system == "Windows":
        appdata = Path(sys.executable).drive  # placeholder, replaced below
        appdata_env = (
            Path.home() / "AppData" / "Roaming" / "Blender Foundation" / "Blender"
        )
        roots.append(appdata_env)
        # Some installs use the portable layout next to blender.exe — we can't auto-detect that,
        # so we skip it. The user can paste the path manually.
    elif system == "Darwin":
        roots.append(home / "Library" / "Application Support" / "Blender")
    else:  # Linux / *BSD
        roots.append(home / ".config" / "blender")
        # Snap / flatpak fallback
        roots.append(home / "snap" / "blender" / "current" / ".config" / "blender")
        roots.append(home / ".var" / "app" / "org.blender.Blender" / "config" / "blender")
    return [r for r in roots if r.exists()]


_VERSION_DIR_RE = re.compile(r"^(\d+)\.(\d+)$")


def find_blender_addon_dirs() -> list[BlenderAddonDir]:
    """Return one entry per detected `<root>/<X.Y>/scripts/addons/` directory."""
    found: list[BlenderAddonDir] = []
    for root in _candidate_roots():
        for child in sorted(root.iterdir(), reverse=True):
            if not child.is_dir():
                continue
            if not _VERSION_DIR_RE.match(child.name):
                continue
            addons = child / "scripts" / "addons"
            if not addons.exists():
                # Older / pristine installs may not have created it yet — still a valid target.
                pass
            installed = read_installed_version(addons / ADDON_FILE_NAME)
            found.append(
                BlenderAddonDir(
                    version=child.name,
                    path=addons,
                    installed_version=installed,
                )
            )
    return found


def read_installed_version(file_path: Path) -> str:
    if not file_path.exists():
        return ""
    try:
        head = file_path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return ""
    m = VERSION_RE.search(head)
    if not m:
        return ""
    return ".".join(m.groups())


def read_bundled_version() -> str:
    return read_installed_version(BUNDLED_ADDON_PATH)


def read_addon_name(file_path: Path = BUNDLED_ADDON_PATH) -> str:
    if not file_path.exists():
        return "MCP Server"
    try:
        head = file_path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return "MCP Server"
    m = NAME_RE.search(head)
    return m.group(1) if m else "MCP Server"


# ---------------------------------------------------------------- install


def fetch_remote_addon(timeout: float = 10.0) -> bytes:
    """Download the latest addon source from the official repo."""
    r = requests.get(ADDON_REMOTE_URL, timeout=timeout)
    r.raise_for_status()
    return r.content


def install_addon(target: BlenderAddonDir, source: str = "remote") -> Path:
    """Write the addon file to `target.path / blender_mcp_addon.py`.

    `source`:
        "remote"  — download from GitHub (falls back to bundled on failure)
        "bundled" — always use the file shipped with the app
    """
    target.path.mkdir(parents=True, exist_ok=True)
    dest = target.addon_file

    payload: bytes
    if source == "bundled":
        payload = BUNDLED_ADDON_PATH.read_bytes()
    else:
        try:
            payload = fetch_remote_addon()
        except Exception:
            if not BUNDLED_ADDON_PATH.exists():
                raise
            payload = BUNDLED_ADDON_PATH.read_bytes()

    if dest.exists():
        backup = dest.with_suffix(dest.suffix + ".bak")
        try:
            shutil.copy2(dest, backup)
        except OSError:
            pass

    dest.write_bytes(payload)
    target.installed_version = read_installed_version(dest)
    return dest


def uninstall_addon(target: BlenderAddonDir) -> bool:
    if target.addon_file.exists():
        try:
            target.addon_file.unlink()
            target.installed_version = ""
            return True
        except OSError:
            return False
    return False


def open_addon_dir(target: BlenderAddonDir) -> bool:
    """Open the addon directory in the OS file explorer."""
    target.path.mkdir(parents=True, exist_ok=True)
    try:
        if platform.system() == "Windows":
            import os
            os.startfile(str(target.path))  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            import subprocess
            subprocess.Popen(["open", str(target.path)])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(target.path)])
        return True
    except Exception:
        return False
