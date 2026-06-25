from __future__ import annotations

import atexit
import csv
import ctypes
import io
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import threading
import time
from typing import Any
import winreg
from ctypes import wintypes

import pythoncom
import win32com.client
from mcp.server.fastmcp import FastMCP

SERVER_NAME = "solidworks"
SOLIDWORKS_PROG_ID = "SldWorks.Application"
REPO_ROOT = Path(__file__).resolve().parents[2]

# === Skill 模块导入 ===
SKILL_SCRIPTS_DIR = REPO_ROOT.parent / "solidworks-automation-skill" / "scripts"
if str(SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS_DIR))

from sw_connect import mm as skill_mm, deg as skill_deg, connect_solidworks as skill_connect
from sw_preflight import missing_com_dependencies, solidworks_installed
from sw_assembly import (
    add_component as skill_add_component,
    add_concentric_mate_by_cylinders,
    add_mate5_checked,
    find_component_by_name,
    get_component_feature_entity,
    select_entities_for_mate,
    get_components,
    resolve_component,
    collect_mate_feature_summary,
    SW_MATE_COINCIDENT,
    SW_MATE_CONCENTRIC,
    SW_MATE_DISTANCE,
)
from sw_export import (
    export_to_step, export_to_stl, export_to_iges,
    export_to_parasolid, export_to_pdf, export_to_dxf,
)
from sw_appearance import set_component_appearance, set_document_appearance
from sw_review import run_review
from sw_motion import (
    create_motion_study as skill_create_motion_study,
    add_constant_speed_rotary_motor_by_cylinders,
    calculate_and_play,
    ensure_motion_type_library,
)
try:
    import comtypes
except ImportError:
    comtypes = None

BRIDGE_DLL = Path(
    os.environ.get(
        "SOLIDWORKS_MCP_BRIDGE_DLL",
        str(REPO_ROOT / "bridge" / "bin" / "Release" / "net8.0-windows" / "SolidWorksBridge.dll"),
    )
)
DEFAULT_PART_TEMPLATE = Path(
    os.environ.get(
        "SOLIDWORKS_MCP_TEMPLATE",
        r"C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2023\templates\gb_part.prtdot",
    )
)

DEFAULT_ASSEMBLY_TEMPLATE = Path(
    os.environ.get(
        "SOLIDWORKS_MCP_ASSEMBLY_TEMPLATE",
        r"C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2025\templates\gb_assembly.asmdot",
    )
)

DOC_TYPE_BY_SUFFIX = {
    ".sldprt": 1,
    ".sldasm": 2,
    ".slddrw": 3,
}


mcp = FastMCP("SolidWorks MCP")

_sw_global_lock = threading.RLock()
_bridge_lock = threading.Lock()
_bridge_process: subprocess.Popen[str] | None = None
_launch_timeout_seconds = 60.0
_launch_poll_interval_seconds = 0.5
_popup_guard_stop = threading.Event()
_popup_guard_thread: threading.Thread | None = None

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_enum_windows_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_is_window_visible = _user32.IsWindowVisible
_is_window_visible.argtypes = [wintypes.HWND]
_is_window_visible.restype = wintypes.BOOL
_get_window_text_length = _user32.GetWindowTextLengthW
_get_window_text_length.argtypes = [wintypes.HWND]
_get_window_text_length.restype = ctypes.c_int
_get_window_text = _user32.GetWindowTextW
_get_window_text.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_get_window_text.restype = ctypes.c_int
_get_class_name = _user32.GetClassNameW
_get_class_name.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_get_class_name.restype = ctypes.c_int
_show_window = _user32.ShowWindow
_show_window.argtypes = [wintypes.HWND, ctypes.c_int]
_show_window.restype = wintypes.BOOL
_set_foreground_window = _user32.SetForegroundWindow
_set_foreground_window.argtypes = [wintypes.HWND]
_set_foreground_window.restype = wintypes.BOOL
_get_window_rect = _user32.GetWindowRect

SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_RESTORE = 9


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_int),
        ("top", ctypes.c_int),
        ("right", ctypes.c_int),
        ("bottom", ctypes.c_int),
    ]


def _co_initialize() -> None:
    pythoncom.CoInitialize()


def _co_uninitialize() -> None:
    pythoncom.CoUninitialize()


def _window_text(hwnd: int) -> str:
    length = _get_window_text_length(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    _get_window_text(hwnd, buffer, length + 1)
    return buffer.value


def _window_class(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    _get_class_name(hwnd, buffer, 256)
    return buffer.value


def _enumerate_windows() -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []

    @_enum_windows_proc
    def callback(hwnd: int, _lparam: int) -> bool:
        if not _is_window_visible(hwnd):
            return True

        title = _window_text(hwnd)
        if not title:
            return True

        rect = RECT()
        _get_window_rect(hwnd, ctypes.byref(rect))
        windows.append(
            {
                "hwnd": hwnd,
                "title": title,
                "class": _window_class(hwnd),
                "rect": (rect.left, rect.top, rect.right, rect.bottom),
            }
        )
        return True

    _user32.EnumWindows(callback, 0)
    return windows


def _manage_solidworks_popups() -> None:
    main_window: dict[str, Any] | None = None
    hidden_dialog = False

    for window in _enumerate_windows():
        title = window["title"]
        left, top, right, bottom = window["rect"]
        width = right - left
        height = bottom - top

        if title.startswith("SOLIDWORKS Premium"):
            main_window = window
            continue

        if title == "splash" and width <= 600 and height <= 400:
            _show_window(window["hwnd"], SW_HIDE)
            continue

        if title == "SOLIDWORKS" and window["class"] == "#32770" and width <= 500 and height <= 250:
            _show_window(window["hwnd"], SW_HIDE)
            hidden_dialog = True

    if hidden_dialog and main_window is not None:
        _show_window(main_window["hwnd"], SW_RESTORE)
        _set_foreground_window(main_window["hwnd"])


def _popup_guard_loop() -> None:
    while not _popup_guard_stop.wait(1.0):
        try:
            _manage_solidworks_popups()
        except Exception:
            continue


def _ensure_popup_guard() -> None:
    global _popup_guard_thread
    if _popup_guard_thread is not None and _popup_guard_thread.is_alive():
        return

    _popup_guard_thread = threading.Thread(
        target=_popup_guard_loop,
        name="solidworks-popup-guard",
        daemon=True,
    )
    _popup_guard_thread.start()


def _get_app(create: bool = False):
    app = _try_get_active_app()
    if app is None and create:
        _launch_desktop_solidworks()
        app = _wait_for_active_app()
    if app is None:
        return None

    try:
        app.UserControl = True
    except Exception:
        pass
    return app


def _try_get_active_app():
    try:
        dispatch = pythoncom.GetActiveObject(SOLIDWORKS_PROG_ID)
    except pythoncom.com_error:
        return None
    return win32com.client.Dispatch(dispatch.QueryInterface(pythoncom.IID_IDispatch))


def _wait_for_active_app(timeout_seconds: float = _launch_timeout_seconds):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        app = _try_get_active_app()
        if app is not None:
            return app
        time.sleep(_launch_poll_interval_seconds)
    raise TimeoutError("Timed out waiting for SolidWorks to register its COM automation object.")


def _resolve_solidworks_executable() -> str:
    clsid = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, rf"{SOLIDWORKS_PROG_ID}\CLSID").strip()
    local_server = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32").strip()
    if local_server.startswith('"'):
        closing_quote = local_server.find('"', 1)
        if closing_quote > 1:
            return local_server[1:closing_quote]
    exe_marker = local_server.lower().find(".exe")
    if exe_marker >= 0:
        return local_server[: exe_marker + 4]
    return local_server


def _launch_desktop_solidworks() -> None:
    executable = _resolve_solidworks_executable()
    os.startfile(executable)


def _is_running() -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq SLDWORKS.exe"],
        capture_output=True,
        text=True,
        check=False,
    )
    return "SLDWORKS.exe" in result.stdout


def _sldworks_pids() -> list[int]:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq SLDWORKS.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []

    reader = csv.reader(io.StringIO(result.stdout))
    pids: list[int] = []
    for row in reader:
        if len(row) < 2 or row[0] == "INFO:":
            continue
        try:
            pids.append(int(row[1]))
        except ValueError:
            continue
    return pids


def _bool_value(value: Any) -> bool:
    return bool(value)


def _value_or_call(value: Any) -> Any:
    return value() if callable(value) else value


def _mm_to_m(value_mm: float) -> float:
    return value_mm / 1000.0


def _to_mm(value: float, unit: str | None) -> float:
    normalized = (unit or "mm").strip().lower()
    if normalized == "cm":
        return value * 10.0
    if normalized == "m":
        return value * 1000.0
    return value


def _axis_positions(count: int, half_span_mm: float, offset_mm: float) -> list[float]:
    if count <= 0:
        raise ValueError("count must be positive")
    usable = half_span_mm - offset_mm
    if usable < 0:
        raise ValueError("offset exceeds half span")
    if count == 1:
        return [0.0]
    step = (usable * 2.0) / (count - 1)
    return [(-usable + index * step) for index in range(count)]


def _extract_triplet_mm(prompt: str) -> tuple[float, float, float] | None:
    triplet_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*[x×by]{1,2}\s*(\d+(?:\.\d+)?)\s*(?:mm)?\s*[x×by]{1,2}\s*(\d+(?:\.\d+)?)\s*mm?",
        prompt,
        re.IGNORECASE,
    )
    if not triplet_match:
        return None
    return tuple(float(triplet_match.group(index)) for index in range(1, 4))


def _extract_secondary_triplet_mm(prompt: str, keywords: list[str]) -> tuple[float, float, float] | None:
    lowered = prompt.lower()
    for keyword in keywords:
        start = lowered.find(keyword.lower())
        if start < 0:
            continue
        triplet = _extract_triplet_mm(prompt[start:])
        if triplet is not None:
            return triplet
    return None


def _extract_value_with_unit(prompt: str, patterns: list[str], default_unit: str = "mm") -> float | None:
    for pattern in patterns:
        match = re.search(pattern, prompt, re.IGNORECASE)
        if not match:
            continue
        value = float(match.group(1))
        unit = match.group(2) if match.lastindex and match.lastindex >= 2 else default_unit
        return _to_mm(value, unit)
    return None


def _extract_grid(prompt: str) -> tuple[int, int] | None:
    grid_match = re.search(r"(\d+)\s*(?:x|×|by)\s*(\d+)\s*grid", prompt, re.IGNORECASE)
    if not grid_match:
        grid_match = re.search(r"(\d+)\s*(?:x|×|by)\s*(\d+)", prompt, re.IGNORECASE)
    if not grid_match:
        return None
    return int(grid_match.group(1)), int(grid_match.group(2))


def _extract_first_mm(prompt: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, prompt, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _desktop_path() -> Path:
    return Path.home() / "Desktop"


def _default_part_save_path(base_name: str) -> Path:
    safe = re.sub(r'[<>:"/\\|?*]+', "_", base_name).strip() or "solidworks_part"
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return _desktop_path() / f"{safe}-{timestamp}.SLDPRT"


def _contains_any(prompt: str, keywords: list[str]) -> bool:
    lower = prompt.lower()
    return any(keyword.lower() in lower for keyword in keywords)


def _composite_result(step_results: dict[str, Any], **extra: Any) -> dict[str, Any]:
    def _step_ok(result: Any) -> bool:
        if isinstance(result, list):
            return all(_step_ok(item) for item in result)
        if isinstance(result, dict):
            if "ok" in result:
                return bool(result["ok"])
            if "opened" in result:
                return bool(result["opened"])
            if result.get("running") and isinstance(result.get("active_document"), dict):
                return bool(result["active_document"].get("has_document"))
            return result.get("ok", result.get("opened", False))
        return False

    ok = all(_step_ok(result) for result in step_results.values())
    response = {"ok": ok, "steps": step_results}
    response.update(extra)
    return response


def _shutdown_bridge() -> None:
    global _bridge_process
    if _bridge_process is None:
        return

    if _bridge_process.poll() is None:
        try:
            if _bridge_process.stdin:
                _bridge_process.stdin.close()
        except Exception:
            pass
        _bridge_process.terminate()
        try:
            _bridge_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _bridge_process.kill()
            _bridge_process.wait(timeout=5)

    _bridge_process = None


def _shutdown_popup_guard() -> None:
    _popup_guard_stop.set()


def _get_bridge_process() -> subprocess.Popen[str]:
    global _bridge_process
    if _bridge_process is not None and _bridge_process.poll() is None:
        return _bridge_process

    _shutdown_bridge()
    _bridge_process = subprocess.Popen(
        ["dotnet", str(BRIDGE_DLL), "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    return _bridge_process


atexit.register(_shutdown_bridge)
atexit.register(_shutdown_popup_guard)
_ensure_popup_guard()


def _run_bridge(command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not BRIDGE_DLL.exists():
        return {
            "ok": False,
            "reason": "bridge_missing",
            "bridge_path": str(BRIDGE_DLL),
        }

    request = json.dumps({"command": command, "payload": payload or {}}, ensure_ascii=False)

    with _sw_global_lock:
        with _bridge_lock:
            process = _get_bridge_process()
            if process.stdin is None or process.stdout is None:
                _shutdown_bridge()
                return {
                    "ok": False,
                    "reason": "bridge_missing_stdio",
                    "command": command,
                }

            try:
                process.stdin.write(request + "\n")
                process.stdin.flush()
                stdout = process.stdout.readline()
            except Exception as exc:
                _shutdown_bridge()
                return {
                    "ok": False,
                    "reason": "bridge_io_failed",
                    "command": command,
                    "detail": str(exc),
                }

            if not stdout:
                stderr = ""
                if process.stderr is not None:
                    stderr = process.stderr.read().strip()
                returncode = process.poll()
                _shutdown_bridge()
                return {
                    "ok": False,
                    "reason": "bridge_command_failed",
                    "command": command,
                    "returncode": returncode,
                    "stdout": "",
                    "stderr": stderr,
                }

        stdout = stdout.strip()
        try:
            return json.loads(stdout or "{}")
        except json.JSONDecodeError:
            return {
                "ok": False,
                "reason": "bridge_invalid_json",
                "command": command,
                "stdout": stdout,
                "stderr": "",
            }


def _doc_summary(doc: Any) -> dict[str, Any]:
    if doc is None:
        return {"has_document": False}

    title = _value_or_call(getattr(doc, "GetTitle", None))
    path_name = _value_or_call(getattr(doc, "GetPathName", None))
    doc_type = _value_or_call(getattr(doc, "GetType", None))
    return {
        "has_document": True,
        "title": title,
        "path": path_name,
        "doc_type": doc_type,
    }


@mcp.tool()
def ping() -> dict[str, str]:
    """Return a simple health response for the SolidWorks MCP server."""
    return {"server": SERVER_NAME, "status": "ok"}


@mcp.tool()
def solidworks_status() -> dict[str, Any]:
    """Return whether SolidWorks is running and basic app/document state."""
    _co_initialize()
    try:
        app = _get_app(create=False)
        if app is None:
            return {"running": False, "visible": False, "active_document": None}

        active_doc = getattr(app, "ActiveDoc", None)
        return {
            "running": True,
            "visible": _bool_value(getattr(app, "Visible", False)),
            "revision": _value_or_call(getattr(app, "RevisionNumber", None)),
            "active_document": _doc_summary(active_doc),
        }
    finally:
        _co_uninitialize()


@mcp.tool()
def launch_solidworks(visible: bool = True) -> dict[str, Any]:
    """Launch or attach to SolidWorks and optionally show its UI."""
    _co_initialize()
    try:
        app = _get_app(create=True)
        app.Visible = visible
        active_doc = getattr(app, "ActiveDoc", None)
        return {
            "running": True,
            "visible": _bool_value(getattr(app, "Visible", False)),
            "revision": _value_or_call(getattr(app, "RevisionNumber", None)),
            "active_document": _doc_summary(active_doc),
        }
    finally:
        _co_uninitialize()


@mcp.tool()
def close_solidworks(force: bool = True) -> dict[str, Any]:
    """Close the running SolidWorks instance if one is active."""
    _shutdown_bridge()
    pids = _sldworks_pids()
    if not pids:
        return {"closed": False, "reason": "not_running", "force": force}

    command = ["taskkill"]
    for pid in pids:
        command.extend(["/PID", str(pid)])
    command.append("/T")
    if force:
        command.append("/F")

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    time.sleep(1)
    remaining = _sldworks_pids()
    return {
        "closed": not remaining,
        "force": force,
        "requestedPids": pids,
        "remainingPids": remaining,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


@mcp.tool()
def active_document() -> dict[str, Any]:
    """Return metadata about the current active SolidWorks document."""
    _co_initialize()
    try:
        app = _get_app(create=False)
        if app is None:
            return {"running": False, "active_document": None}

        active_doc = getattr(app, "ActiveDoc", None)
        return {"running": True, "active_document": _doc_summary(active_doc)}
    finally:
        _co_uninitialize()


@mcp.tool()
def save_active_document(path: str | None = None, base_name: str = "solidworks-part") -> dict[str, Any]:
    """Save the active SolidWorks part to an explicit path or to the Desktop with a generated name."""
    resolved = Path(path).expanduser().resolve() if path else _default_part_save_path(base_name)
    if resolved.suffix.lower() != ".sldprt":
        resolved = resolved.with_suffix(".SLDPRT")
    resolved.parent.mkdir(parents=True, exist_ok=True)

    _co_initialize()
    try:
        app = _get_app(create=False)
        if app is None:
            return {"ok": False, "reason": "not_running", "path": str(resolved)}

        doc = getattr(app, "ActiveDoc", None)
        if doc is None:
            return {"ok": False, "reason": "no_active_document", "path": str(resolved)}

        errors = 0
        warnings = 0
        saved = False
        save_method = None

        try:
            saved = bool(doc.SaveAs3(str(resolved), 0, 2))
            save_method = "ModelDoc2.SaveAs3"
        except Exception:
            try:
                saved = bool(doc.Extension.SaveAs(str(resolved), 0, 2, None, errors, warnings))
                save_method = "ModelDocExtension.SaveAs"
            except Exception as exc:
                return {
                    "ok": False,
                    "reason": "save_failed",
                    "path": str(resolved),
                    "detail": str(exc),
                }

        return {
            "ok": saved or resolved.exists(),
            "path": str(resolved),
            "method": save_method,
            "savedFlag": saved,
            "exists": resolved.exists(),
            "active_document": _doc_summary(doc),
        }
    finally:
        _co_uninitialize()


@mcp.tool()
def open_document(path: str, visible: bool = True) -> dict[str, Any]:
    """Open a SolidWorks part, assembly, or drawing by file path."""
    resolved = Path(path).expanduser().resolve()
    suffix = resolved.suffix.lower()
    doc_type = DOC_TYPE_BY_SUFFIX.get(suffix)
    if doc_type is None:
        return {
            "opened": False,
            "reason": "unsupported_extension",
            "supported_extensions": sorted(DOC_TYPE_BY_SUFFIX),
        }

    if not resolved.exists():
        return {"opened": False, "reason": "file_not_found", "path": str(resolved)}

    _co_initialize()
    try:
        app = _get_app(create=True)
        app.Visible = visible
        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        doc = app.OpenDoc6(str(resolved), doc_type, 0, "", errors, warnings)
        return {
            "opened": doc is not None,
            "path": str(resolved),
            "visible": _bool_value(getattr(app, "Visible", False)),
            "errors": int(errors.value),
            "warnings": int(warnings.value),
            "active_document": _doc_summary(doc),
        }
    finally:
        _co_uninitialize()


@mcp.tool()
def new_part(template_path: str | None = None) -> dict[str, Any]:
    """Create a new SolidWorks part from a template."""
    payload: dict[str, Any] = {}
    if template_path:
        payload["templatePath"] = str(Path(template_path).expanduser().resolve())
    else:
        payload["templatePath"] = str(DEFAULT_PART_TEMPLATE)
    return _run_bridge("new_part", payload)


@mcp.tool()
def create_sketch_on_plane(plane: str = "front") -> dict[str, Any]:
    """Start editing a sketch on the given base plane."""
    return _run_bridge("create_sketch_on_plane", {"plane": plane})


@mcp.tool()
def create_center_rectangle(
    center_x: float,
    center_y: float,
    corner_x: float,
    corner_y: float,
    center_z: float = 0.0,
    corner_z: float = 0.0,
) -> dict[str, Any]:
    """Create a center rectangle in the current sketch."""
    return _run_bridge(
        "create_center_rectangle",
        {
            "centerX": center_x,
            "centerY": center_y,
            "centerZ": center_z,
            "cornerX": corner_x,
            "cornerY": corner_y,
            "cornerZ": corner_z,
        },
    )


@mcp.tool()
def create_circle(
    center_x: float,
    center_y: float,
    radius: float,
    center_z: float = 0.0,
) -> dict[str, Any]:
    """Create a circle in the current sketch."""
    return _run_bridge(
        "create_circle",
        {
            "centerX": center_x,
            "centerY": center_y,
            "centerZ": center_z,
            "radius": radius,
        },
    )


@mcp.tool()
def add_dimension(
    orientation: str,
    location_x: float,
    location_y: float,
    location_z: float = 0.0,
    segment_index: int = 0,
    entity_name: str | None = None,
    method: str = "macro",
) -> dict[str, Any]:
    """Add a sketch dimension using the in-process macro path or the direct diagnostic path."""
    payload: dict[str, Any] = {
        "orientation": orientation,
        "locationX": location_x,
        "locationY": location_y,
        "locationZ": location_z,
        "segmentIndex": segment_index,
        "method": method,
    }
    if entity_name:
        payload["entityName"] = entity_name
    return _run_bridge("add_dimension", payload)


@mcp.tool()
def extrude_boss(depth: float) -> dict[str, Any]:
    """Extrude the latest sketch as a boss feature."""
    return _run_bridge("extrude_boss", {"depth": depth})


@mcp.tool()
def extrude_cut(depth: float, through_all: bool = True) -> dict[str, Any]:
    """Extrude the latest sketch as a cut feature."""
    return _run_bridge("extrude_cut", {"depth": depth, "throughAll": through_all})


@mcp.tool()
def inspect_active_part() -> dict[str, Any]:
    """Inspect the active part and return feature and body summaries."""
    return _run_bridge("inspect_active_part", {})


@mcp.tool()
def apply_fillet_to_feature_edges(feature_name: str, radius: float, z_min: float | None = None, z_max: float | None = None) -> dict[str, Any]:
    """Apply a constant-radius fillet to the edges owned by a named feature, optionally filtered by edge bbox midpoint Z (in meters)."""
    payload: dict[str, Any] = {"featureName": feature_name, "radius": radius}
    if z_min is not None:
        payload["zMin"] = z_min
    if z_max is not None:
        payload["zMax"] = z_max
    return _run_bridge("apply_fillet_to_feature_edges", payload)


@mcp.tool()
def apply_chamfer_to_feature_edges(feature_name: str, distance: float) -> dict[str, Any]:
    """Apply an equal-distance chamfer to the edges owned by a named feature."""
    return _run_bridge(
        "apply_chamfer_to_feature_edges",
        {
            "featureName": feature_name,
            "distance": distance,
        },
    )


@mcp.tool()
def combine_all_bodies() -> dict[str, Any]:
    """Combine all solid bodies in the active part with an add/union operation."""
    return _run_bridge("combine_all_bodies", {})


@mcp.tool()
def run_macro(
    macro_path: str,
    module_name: str = "",
    procedure_name: str = "",
    options: int = 0,
) -> dict[str, Any]:
    """Compatibility stub kept to preserve the original MCP surface without invoking unstable macro loaders."""
    requested_path = str(Path(macro_path).expanduser())
    return {
        "ok": False,
        "reason": "run_macro_disabled_on_host",
        "macroPath": requested_path,
        "moduleName": module_name,
        "procedureName": procedure_name,
        "options": options,
        "recommendedMethod": "create_rectangular_block|create_plate_with_holes|design_from_prompt",
        "detail": (
            "SolidWorks macro execution is disabled on this host because the .NET/VSTA macro "
            "loader can raise a Microsoft .NET Framework dialog and terminate SolidWorks."
        ),
    }


# Phase 1: Sketch Entities

@mcp.tool()
def draw_line(
    x1: float, y1: float, x2: float, y2: float,
    z1: float = 0.0, z2: float = 0.0,
) -> dict[str, Any]:
    """Draw a line segment in the active sketch. Coordinates in meters."""
    return _run_bridge("draw_line", {
        "x1": x1, "y1": y1, "z1": z1,
        "x2": x2, "y2": y2, "z2": z2,
    })


@mcp.tool()
def draw_arc(
    center_x: float, center_y: float,
    start_x: float, start_y: float,
    end_x: float, end_y: float,
    direction: int = 1,
    center_z: float = 0.0,
    start_z: float = 0.0,
    end_z: float = 0.0,
) -> dict[str, Any]:
    """Draw an arc in the active sketch. direction: 1=CCW, -1=CW. Coordinates in meters."""
    return _run_bridge("draw_arc", {
        "centerX": center_x, "centerY": center_y, "centerZ": center_z,
        "startX": start_x, "startY": start_y, "startZ": start_z,
        "endX": end_x, "endY": end_y, "endZ": end_z,
        "direction": direction,
    })


@mcp.tool()
def draw_polygon(
    center_x: float, center_y: float, radius: float,
    sides: int, inscribed: bool = False,
    center_z: float = 0.0,
) -> dict[str, Any]:
    """Draw a regular polygon in the active sketch. Coordinates in meters."""
    return _run_bridge("draw_polygon", {
        "centerX": center_x, "centerY": center_y, "centerZ": center_z,
        "radius": radius, "sides": sides, "inscribed": inscribed,
    })


@mcp.tool()
def draw_centerline(
    x1: float, y1: float, x2: float, y2: float,
    z1: float = 0.0, z2: float = 0.0,
) -> dict[str, Any]:
    """Draw a centerline (construction line) in the active sketch. Coordinates in meters."""
    return _run_bridge("draw_centerline", {
        "x1": x1, "y1": y1, "z1": z1,
        "x2": x2, "y2": y2, "z2": z2,
    })


@mcp.tool()
def create_sketch_on_face(face_name: str, face_x: float = 0.0, face_y: float = 0.0, face_z: float = 0.0) -> dict[str, Any]:
    """Start editing a sketch on a model face identified by name or position (face_x/y/z in meters)."""
    return _run_bridge("create_sketch_on_face", {
        "faceName": face_name,
        "faceX": face_x,
        "faceY": face_y,
        "faceZ": face_z,
    })


# Phase 2: Advanced Features

@mcp.tool()
def create_ref_plane(
    ref1: str, constraint1: int, offset1: float = 0.0,
    ref2: str | None = None, constraint2: int = 0, offset2: float = 0.0,
    ref3: str | None = None, constraint3: int = 0, offset3: float = 0.0,
) -> dict[str, Any]:
    """Create a reference plane. constraint values: 1=Distance, 2=Angle, 4=Parallel, 8=Perpendicular, 16=MidPlane. Offset in meters."""
    payload: dict[str, Any] = {"ref1": ref1, "constraint1": constraint1, "offset1": offset1}
    if ref2 is not None:
        payload["ref2"] = ref2
        payload["constraint2"] = constraint2
        payload["offset2"] = offset2
    if ref3 is not None:
        payload["ref3"] = ref3
        payload["constraint3"] = constraint3
        payload["offset3"] = offset3
    return _run_bridge("create_ref_plane", payload)


@mcp.tool()
def mirror_feature(
    mirror_plane: str, features: list[str],
    geom_pattern: bool = False, merge: bool = True,
) -> dict[str, Any]:
    """Mirror features across a plane. mirror_plane is the plane name, features is a list of feature names."""
    return _run_bridge("mirror_feature", {
        "mirrorPlane": mirror_plane,
        "features": features,
        "geomPattern": geom_pattern,
        "merge": merge,
    })


@mcp.tool()
def circular_pattern(
    axis: str, count: int, angle: float,
    features: list[str], equal_spacing: bool = True,
) -> dict[str, Any]:
    """Create a circular pattern. axis is the axis name, angle in radians, features is list of feature names."""
    return _run_bridge("circular_pattern", {
        "axis": axis, "count": count, "angle": angle,
        "features": features, "equalSpacing": equal_spacing,
    })


@mcp.tool()
def linear_pattern(
    direction1: str, d1_count: int, d1_spacing: float,
    features: list[str],
    direction2: str | None = None, d2_count: int = 1, d2_spacing: float = 0.0,
) -> dict[str, Any]:
    """Create a linear pattern. Spacing in meters. direction1/direction2 are edge or axis names."""
    payload: dict[str, Any] = {
        "direction1": direction1, "d1Count": d1_count, "d1Spacing": d1_spacing,
        "features": features,
    }
    if direction2 is not None:
        payload["direction2"] = direction2
        payload["d2Count"] = d2_count
        payload["d2Spacing"] = d2_spacing
    return _run_bridge("linear_pattern", payload)


@mcp.tool()
def loft_boss(
    profiles: list[str],
    guides: list[str] | None = None,
    merge_result: bool = True,
) -> dict[str, Any]:
    """Create a loft boss feature. profiles and guides are sketch feature names."""
    payload: dict[str, Any] = {"profiles": profiles, "mergeResult": merge_result}
    if guides is not None:
        payload["guides"] = guides
    return _run_bridge("loft_boss", payload)


@mcp.tool()
def sweep_boss(
    profile: str, path: str, merge_result: bool = True,
) -> dict[str, Any]:
    """Create a sweep boss feature. profile and path are sketch feature names."""
    return _run_bridge("sweep_boss", {
        "profile": profile, "path": path, "mergeResult": merge_result,
    })


@mcp.tool()
def rib(
    sketch: str, thickness: float,
    thickness_type: int = 0, flip: bool = False, draft_enable: bool = False,
) -> dict[str, Any]:
    """Create a rib feature. thickness in meters. thickness_type: 0=OneSide, 1=TwoSides, 2=MidPlane."""
    return _run_bridge("rib", {
        "sketch": sketch, "thickness": thickness,
        "thicknessType": thickness_type, "flip": flip, "draftEnable": draft_enable,
    })


# Phase 3: Assembly

@mcp.tool()
def new_assembly(template_path: str | None = None) -> dict[str, Any]:
    """Create a new SolidWorks assembly from a template."""
    resolved = template_path or str(DEFAULT_ASSEMBLY_TEMPLATE)
    return _run_bridge("new_assembly", {"templatePath": resolved})


@mcp.tool()
def add_component(
    file_path: str, config_name: str = "",
    x: float = 0.0, y: float = 0.0, z: float = 0.0,
) -> dict[str, Any]:
    """Insert a component into the active assembly. Coordinates in meters."""
    return _run_bridge("add_component", {
        "filePath": file_path, "configName": config_name,
        "x": x, "y": y, "z": z,
    })


@mcp.tool()
def add_mate(
    mate_type: int, entities: list[str],
    align_type: int = 0, flip: bool = False,
    distance: float = 0.0, angle: float = 0.0,
) -> dict[str, Any]:
    """Add a mate in the active assembly. mate_type: 0=coincident, 1=concentric, 2=perpendicular, 3=parallel, 4=tangent, 5=distance, 6=angle. entities: list of 'name@type' strings."""
    return _run_bridge("add_mate", {
        "mateType": mate_type, "entities": entities,
        "alignType": align_type, "flip": flip,
        "distance": distance, "angle": angle,
    })


@mcp.tool()
def add_explode_step(
    components: list[str],
    dx: float = 0.0, dy: float = 0.0, dz: float = 0.0,
) -> dict[str, Any]:
    """Create an auto-explode view for selected components. Translation in meters."""
    return _run_bridge("add_explode_step", {
        "components": components, "dx": dx, "dy": dy, "dz": dz,
    })


@mcp.tool()
def add_dimension_v2(
    entities: list[str],
    x: float = 0.0, y: float = 0.0, z: float = 0.0,
) -> dict[str, Any]:
    """Add a smart dimension to selected sketch entities. entities: list of 'name@type' strings. Position (x,y,z) is where the dimension text appears."""
    return _run_bridge("add_dimension_v2", {
        "entities": entities, "x": x, "y": y, "z": z,
    })


@mcp.tool()
def get_mass_properties() -> dict[str, Any]:
    """Get mass, volume, surface area, and center of mass of the active part."""
    return _run_bridge("get_mass_properties", {})


# Phase 4: Export & Analysis

@mcp.tool()
def export_file(output_path: str, version: int = 0, options: int = 0) -> dict[str, Any]:
    """Export the active document. Supported formats: STEP, IGES, STL, Parasolid, PDF (drawings). Output path determines format by extension."""
    return _run_bridge("export_file", {
        "outputPath": output_path, "version": version, "options": options,
    })


@mcp.tool()
def check_interference(coincidence_is_interference: bool = False) -> dict[str, Any]:
    """Check for interferences in the active assembly."""
    return _run_bridge("check_interference", {
        "coincidenceIsInterference": coincidence_is_interference,
    })


@mcp.tool()
def measure_distance(entities: list[str]) -> dict[str, Any]:
    """Measure distance/angle between entities. entities: list of 'name@type' strings."""
    return _run_bridge("measure_distance", {"entities": entities})


@mcp.tool()
def set_material(material: str, database: str = "", config: str = "") -> dict[str, Any]:
    """Set the material of the active part. database: material database name (e.g. 'SOLIDWORKS Materials'). material: material name (e.g. '1060 Alloy')."""
    return _run_bridge("set_material", {
        "database": database, "material": material, "config": config,
    })


def create_spur_gear(
    module_mm: float,
    teeth: int,
    pressure_angle_deg: float = 20.0,
    face_width_mm: float = 20.0,
    bore_diameter_mm: float = 0.0,
    save_path: str | None = None,
) -> dict[str, Any]:
    """Create an accurate spur gear using involute approximation and circular pattern."""
    payload: dict[str, Any] = {
        "moduleMm": module_mm,
        "teeth": teeth,
        "pressureAngleDeg": pressure_angle_deg,
        "faceWidthMm": face_width_mm,
        "boreDiameterMm": bore_diameter_mm,
    }
    if save_path:
        payload["savePath"] = str(Path(save_path).expanduser().resolve())
    return _run_bridge("create_spur_gear", payload)


@mcp.tool()
def create_gear_assembly(
    pinion_path: str,
    gear_path: str,
    center_distance_mm: float,
    save_path: str | None = None,
) -> dict[str, Any]:
    """Create a gear assembly with two spur gears at a given center distance."""
    payload: dict[str, Any] = {
        "pinionPath": str(Path(pinion_path).expanduser().resolve()),
        "gearPath": str(Path(gear_path).expanduser().resolve()),
        "centerDistanceMm": center_distance_mm,
    }
    if save_path:
        payload["savePath"] = str(Path(save_path).expanduser().resolve())
    return _run_bridge("create_gear_assembly", payload)


@mcp.tool()
def create_motion_study(
    speed_rpm: float = 60.0,
    component_name: str = "pinion",
    axis_name: str | None = None,
) -> dict[str, Any]:
    """Create a basic motion study with a rotary motor on a component axis."""
    payload: dict[str, Any] = {
        "speedRpm": speed_rpm,
        "componentName": component_name,
    }
    if axis_name:
        payload["axisName"] = axis_name
    return _run_bridge("create_motion_study", payload)


@mcp.tool()
def create_rectangular_block(
    width_mm: float,
    height_mm: float,
    depth_mm: float,
    plane: str = "front",
    template_path: str | None = None,
) -> dict[str, Any]:
    """Create a rectangular block part from millimeter dimensions."""
    steps: dict[str, Any] = {}
    steps["new_part"] = new_part(template_path=template_path)
    if not steps["new_part"].get("ok"):
        return _composite_result(steps, widthMm=width_mm, heightMm=height_mm, depthMm=depth_mm)

    steps["create_sketch_on_plane"] = create_sketch_on_plane(plane=plane)
    if not steps["create_sketch_on_plane"].get("ok"):
        return _composite_result(steps, widthMm=width_mm, heightMm=height_mm, depthMm=depth_mm)

    steps["create_center_rectangle"] = create_center_rectangle(
        center_x=0.0,
        center_y=0.0,
        corner_x=_mm_to_m(width_mm / 2.0),
        corner_y=_mm_to_m(height_mm / 2.0),
    )
    if not steps["create_center_rectangle"].get("ok"):
        return _composite_result(steps, widthMm=width_mm, heightMm=height_mm, depthMm=depth_mm)

    steps["extrude_boss"] = extrude_boss(depth=_mm_to_m(depth_mm))
    steps["active_document"] = active_document()
    return _composite_result(steps, widthMm=width_mm, heightMm=height_mm, depthMm=depth_mm)


@mcp.tool()
def create_plate_with_holes(
    width_mm: float,
    height_mm: float,
    thickness_mm: float,
    hole_diameter_mm: float,
    offset_x_mm: float,
    offset_y_mm: float,
    rows: int = 2,
    columns: int = 2,
    plane: str = "front",
    template_path: str | None = None,
) -> dict[str, Any]:
    """Create a rectangular plate with an array of through holes."""
    if rows <= 0 or columns <= 0:
        return {"ok": False, "reason": "invalid_hole_grid", "rows": rows, "columns": columns}

    try:
        x_positions_mm = _axis_positions(columns, width_mm / 2.0, offset_x_mm)
        y_positions_mm = _axis_positions(rows, height_mm / 2.0, offset_y_mm)
    except ValueError as exc:
        return {"ok": False, "reason": "invalid_hole_offsets", "detail": str(exc)}

    steps: dict[str, Any] = {}
    steps["new_part"] = new_part(template_path=template_path)
    if not steps["new_part"].get("ok"):
        return _composite_result(steps, holeCount=0)

    steps["create_sketch_on_plane"] = create_sketch_on_plane(plane=plane)
    if not steps["create_sketch_on_plane"].get("ok"):
        return _composite_result(steps, holeCount=0)

    steps["create_center_rectangle"] = create_center_rectangle(
        center_x=0.0,
        center_y=0.0,
        corner_x=_mm_to_m(width_mm / 2.0),
        corner_y=_mm_to_m(height_mm / 2.0),
    )
    if not steps["create_center_rectangle"].get("ok"):
        return _composite_result(steps, holeCount=0)

    steps["extrude_boss"] = extrude_boss(depth=_mm_to_m(thickness_mm))
    if not steps["extrude_boss"].get("ok"):
        steps["active_document"] = active_document()
        return _composite_result(
            steps,
            holeCount=0,
            widthMm=width_mm,
            heightMm=height_mm,
            thicknessMm=thickness_mm,
            holeDiameterMm=hole_diameter_mm,
            rows=rows,
            columns=columns,
        )

    steps["create_hole_sketch"] = create_sketch_on_plane(plane=plane)
    if not steps["create_hole_sketch"].get("ok"):
        steps["active_document"] = active_document()
        return _composite_result(
            steps,
            holeCount=0,
            widthMm=width_mm,
            heightMm=height_mm,
            thicknessMm=thickness_mm,
            holeDiameterMm=hole_diameter_mm,
            rows=rows,
            columns=columns,
        )

    hole_results: list[dict[str, Any]] = []
    radius_m = _mm_to_m(hole_diameter_mm / 2.0)
    for y_mm in y_positions_mm:
        for x_mm in x_positions_mm:
            circle_result = create_circle(
                center_x=_mm_to_m(x_mm),
                center_y=_mm_to_m(y_mm),
                radius=radius_m,
            )
            hole_results.append(circle_result)
            if not circle_result.get("ok"):
                steps["create_circles"] = hole_results
                return _composite_result(steps, holeCount=len(hole_results))

    steps["create_circles"] = hole_results
    steps["extrude_cut"] = extrude_cut(depth=_mm_to_m(thickness_mm * 2.0), through_all=True)
    steps["active_document"] = active_document()
    return _composite_result(
        steps,
        holeCount=len(hole_results),
        widthMm=width_mm,
        heightMm=height_mm,
        thicknessMm=thickness_mm,
        holeDiameterMm=hole_diameter_mm,
        rows=rows,
        columns=columns,
    )


@mcp.tool()
def create_feature_showcase_part(
    base_width_mm: float = 120.0,
    base_height_mm: float = 80.0,
    base_thickness_mm: float = 12.0,
    hole_diameter_mm: float = 6.0,
    hole_offset_mm: float = 15.0,
    rows: int = 2,
    columns: int = 2,
    boss_width_mm: float = 50.0,
    boss_height_mm: float = 30.0,
    boss_thickness_mm: float = 8.0,
    boss_offset_x_mm: float = 18.0,
    boss_offset_y_mm: float = 0.0,
    fillet_radius_mm: float = 3.0,
    chamfer_distance_mm: float = 2.0,
    plane: str = "front",
    template_path: str | None = None,
) -> dict[str, Any]:
    """Create a showcase part that exercises boss, cut, fillet, chamfer, and combine workflows."""
    steps: dict[str, Any] = {}

    steps["new_part"] = new_part(template_path=template_path)
    if not steps["new_part"].get("ok"):
        return _composite_result(steps)

    steps["create_base_sketch"] = create_sketch_on_plane(plane=plane)
    if not steps["create_base_sketch"].get("ok"):
        return _composite_result(steps)

    steps["create_base_rectangle"] = create_center_rectangle(
        center_x=0.0,
        center_y=0.0,
        corner_x=_mm_to_m(base_width_mm / 2.0),
        corner_y=_mm_to_m(base_height_mm / 2.0),
    )
    if not steps["create_base_rectangle"].get("ok"):
        return _composite_result(steps)

    steps["create_base_boss"] = _run_bridge(
        "extrude_boss",
        {
            "depth": _mm_to_m(base_thickness_mm),
            "mergeResult": True,
        },
    )
    if not steps["create_base_boss"].get("ok"):
        steps["inspection"] = inspect_active_part()
        return _composite_result(steps, inspection=steps["inspection"])

    steps["create_hole_sketch"] = create_sketch_on_plane(plane=plane)
    if not steps["create_hole_sketch"].get("ok"):
        steps["inspection"] = inspect_active_part()
        return _composite_result(steps, inspection=steps["inspection"])

    try:
        x_positions_mm = _axis_positions(columns, base_width_mm / 2.0, hole_offset_mm)
        y_positions_mm = _axis_positions(rows, base_height_mm / 2.0, hole_offset_mm)
    except ValueError as exc:
        return {
            "ok": False,
            "reason": "invalid_showcase_hole_offsets",
            "detail": str(exc),
        }

    hole_results: list[dict[str, Any]] = []
    hole_radius_m = _mm_to_m(hole_diameter_mm / 2.0)
    for y_mm in y_positions_mm:
        for x_mm in x_positions_mm:
            hole_results.append(
                create_circle(
                    center_x=_mm_to_m(x_mm),
                    center_y=_mm_to_m(y_mm),
                    radius=hole_radius_m,
                )
            )
    steps["create_holes"] = hole_results
    if not all(result.get("ok") for result in hole_results):
        steps["inspection"] = inspect_active_part()
        return _composite_result(steps, inspection=steps["inspection"])

    steps["create_hole_cut"] = extrude_cut(depth=_mm_to_m(base_thickness_mm * 2.0), through_all=True)
    if not steps["create_hole_cut"].get("ok"):
        steps["inspection"] = inspect_active_part()
        return _composite_result(steps, inspection=steps["inspection"])

    steps["create_boss_sketch"] = create_sketch_on_plane(plane=plane)
    if not steps["create_boss_sketch"].get("ok"):
        steps["inspection"] = inspect_active_part()
        return _composite_result(steps, inspection=steps["inspection"])

    steps["create_secondary_boss_profile"] = create_center_rectangle(
        center_x=_mm_to_m(boss_offset_x_mm),
        center_y=_mm_to_m(boss_offset_y_mm),
        corner_x=_mm_to_m(boss_offset_x_mm + boss_width_mm / 2.0),
        corner_y=_mm_to_m(boss_offset_y_mm + boss_height_mm / 2.0),
    )
    if not steps["create_secondary_boss_profile"].get("ok"):
        steps["inspection"] = inspect_active_part()
        return _composite_result(steps, inspection=steps["inspection"])

    steps["create_secondary_boss"] = _run_bridge(
        "extrude_boss",
        {
            "depth": _mm_to_m(boss_thickness_mm),
            "mergeResult": False,
            "midPlane": False,
        },
    )
    if not steps["create_secondary_boss"].get("ok"):
        steps["inspection"] = inspect_active_part()
        return _composite_result(steps, inspection=steps["inspection"])

    steps["apply_fillet"] = apply_fillet_to_feature_edges(
        feature_name="__last_extrusion__",
        radius=_mm_to_m(fillet_radius_mm),
    )
    if not steps["apply_fillet"].get("ok"):
        steps["inspection"] = inspect_active_part()
        return _composite_result(steps, inspection=steps["inspection"])

    steps["apply_chamfer"] = apply_chamfer_to_feature_edges(
        feature_name="__first_extrusion__",
        distance=_mm_to_m(chamfer_distance_mm),
    )
    if not steps["apply_chamfer"].get("ok"):
        steps["inspection"] = inspect_active_part()
        return _composite_result(steps, inspection=steps["inspection"])

    steps["combine_all_bodies"] = combine_all_bodies()
    steps["inspection"] = inspect_active_part()
    inspection = steps["inspection"]
    combine_result = steps["combine_all_bodies"]
    core_step_names = [
        "new_part",
        "create_base_sketch",
        "create_base_rectangle",
        "create_base_boss",
        "create_hole_sketch",
        "create_hole_cut",
        "create_boss_sketch",
        "create_secondary_boss_profile",
        "create_secondary_boss",
        "apply_fillet",
        "apply_chamfer",
    ]
    core_ok = all(steps[name].get("ok") for name in core_step_names) and all(
        result.get("ok") for result in hole_results
    )
    combine_supported = bool(combine_result.get("ok"))
    response = {
        "ok": core_ok and bool(inspection.get("ok")),
        "steps": steps,
        "inspection": inspection,
        "validation": {
            "bossValidated": bool(steps["create_base_boss"].get("ok")) and bool(steps["create_secondary_boss"].get("ok")),
            "cutValidated": bool(steps["create_hole_cut"].get("ok")),
            "filletValidated": bool(steps["apply_fillet"].get("ok")),
            "chamferValidated": bool(steps["apply_chamfer"].get("ok")),
            "combineValidated": combine_supported,
            "combineSupported": combine_supported,
            "combineStatus": combine_result,
        },
        "holeCount": rows * columns,
        "baseWidthMm": base_width_mm,
        "baseHeightMm": base_height_mm,
        "baseThicknessMm": base_thickness_mm,
        "bossWidthMm": boss_width_mm,
        "bossHeightMm": boss_height_mm,
        "bossThicknessMm": boss_thickness_mm,
        "filletRadiusMm": fillet_radius_mm,
        "chamferDistanceMm": chamfer_distance_mm,
    }
    if not combine_supported:
        response["warnings"] = [
            "combine_all_bodies could not be completed through the current SolidWorks COM host on this machine"
        ]
    return response


@mcp.tool()
def design_from_prompt(prompt: str) -> dict[str, Any]:
    """Interpret a narrow natural-language part request and dispatch to a stable high-level tool."""
    normalized = prompt.strip()
    if not normalized:
        return {"ok": False, "reason": "empty_prompt"}

    triplet = _extract_triplet_mm(normalized)
    showcase_requested = _contains_any(
        normalized,
        ["showcase", "validation", "demo", "fillet", "chamfer", "combine", "boss", "raised"],
    )
    if triplet is None and showcase_requested:
        triplet = (120.0, 80.0, 12.0)
    if triplet is None:
        return {"ok": False, "reason": "dimensions_not_found", "prompt": prompt}

    width_mm, height_mm, depth_or_thickness_mm = triplet
    if showcase_requested:
        boss_triplet = _extract_secondary_triplet_mm(normalized, ["boss", "pad", "raised"])
        if boss_triplet is None:
            boss_triplet = (50.0, 30.0, 8.0)

        hole_diameter_mm = _extract_first_mm(
            normalized,
            [
                r"(?:diameter|dia\.?|鐩村緞)\s*(\d+(?:\.\d+)?)\s*mm?",
                r"m(\d+(?:\.\d+)?)",
            ],
        ) or 6.0
        grid = _extract_grid(normalized) or (2, 2)
        offset_mm = _extract_first_mm(
            normalized,
            [
                r"(?:offset|edge offset|from the nearest .*? edges?|璺濊竟)\s*(\d+(?:\.\d+)?)\s*mm?",
                r"(\d+(?:\.\d+)?)\s*mm?\s*(?:from the nearest .*? edges?|edge offset|璺濊竟)",
            ],
        ) or 15.0
        boss_offset_x_mm = _extract_first_mm(
            normalized,
            [
                r"offset\s*(\d+(?:\.\d+)?)\s*mm?\s*(?:on\s*x|x)",
            ],
        ) or 18.0
        boss_offset_y_mm = _extract_first_mm(
            normalized,
            [
                r"offset\s*(\d+(?:\.\d+)?)\s*mm?\s*(?:on\s*y|y)",
            ],
        ) or 0.0
        fillet_radius_mm = _extract_first_mm(
            normalized,
            [
                r"fillet\s*(\d+(?:\.\d+)?)\s*mm?",
            ],
        ) or 3.0
        chamfer_distance_mm = _extract_first_mm(
            normalized,
            [
                r"chamfer\s*(\d+(?:\.\d+)?)\s*mm?",
            ],
        ) or 2.0

        result = create_feature_showcase_part(
            base_width_mm=width_mm,
            base_height_mm=height_mm,
            base_thickness_mm=depth_or_thickness_mm,
            hole_diameter_mm=hole_diameter_mm,
            hole_offset_mm=offset_mm,
            rows=grid[0],
            columns=grid[1],
            boss_width_mm=boss_triplet[0],
            boss_height_mm=boss_triplet[1],
            boss_thickness_mm=boss_triplet[2],
            boss_offset_x_mm=boss_offset_x_mm,
            boss_offset_y_mm=boss_offset_y_mm,
            fillet_radius_mm=fillet_radius_mm,
            chamfer_distance_mm=chamfer_distance_mm,
        )
        return {
            "ok": result.get("ok", False),
            "shape": "feature_showcase_part",
            "parsed": {
                "baseWidthMm": width_mm,
                "baseHeightMm": height_mm,
                "baseThicknessMm": depth_or_thickness_mm,
                "holeDiameterMm": hole_diameter_mm,
                "rows": grid[0],
                "columns": grid[1],
                "holeOffsetMm": offset_mm,
                "bossWidthMm": boss_triplet[0],
                "bossHeightMm": boss_triplet[1],
                "bossThicknessMm": boss_triplet[2],
                "bossOffsetXMm": boss_offset_x_mm,
                "bossOffsetYMm": boss_offset_y_mm,
                "filletRadiusMm": fillet_radius_mm,
                "chamferDistanceMm": chamfer_distance_mm,
            },
            "result": result,
        }
    has_holes = _contains_any(normalized, ["hole", "holes", "孔", "drill", "through hole"])
    if has_holes:
        hole_diameter_mm = _extract_first_mm(
            normalized,
            [
                r"(?:diameter|dia\.?|直径)\s*(\d+(?:\.\d+)?)\s*mm?",
                r"m(\d+(?:\.\d+)?)",
            ],
        )
        if hole_diameter_mm is None:
            return {"ok": False, "reason": "hole_diameter_not_found", "prompt": prompt}

        grid = _extract_grid(normalized)
        if grid is None:
            hole_count_match = re.search(r"(\d+)\s*(?:holes|孔)", normalized, re.IGNORECASE)
            hole_count = int(hole_count_match.group(1)) if hole_count_match else 4
            grid = (2, 2) if hole_count == 4 else (hole_count, 1)

        offset_mm = _extract_first_mm(
            normalized,
            [
                r"(?:offset|edge offset|from the nearest .*? edges?|距边)\s*(\d+(?:\.\d+)?)\s*mm?",
                r"(\d+(?:\.\d+)?)\s*mm?\s*(?:from the nearest .*? edges?|edge offset|距边)",
            ],
        )
        if offset_mm is None:
            offset_mm = 10.0

        result = create_plate_with_holes(
            width_mm=width_mm,
            height_mm=height_mm,
            thickness_mm=depth_or_thickness_mm,
            hole_diameter_mm=hole_diameter_mm,
            offset_x_mm=offset_mm,
            offset_y_mm=offset_mm,
            rows=grid[0],
            columns=grid[1],
        )
        return {
            "ok": result.get("ok", False),
            "shape": "plate_with_holes",
            "parsed": {
                "widthMm": width_mm,
                "heightMm": height_mm,
                "thicknessMm": depth_or_thickness_mm,
                "holeDiameterMm": hole_diameter_mm,
                "rows": grid[0],
                "columns": grid[1],
                "offsetMm": offset_mm,
            },
            "result": result,
        }

    result = create_rectangular_block(
        width_mm=width_mm,
        height_mm=height_mm,
        depth_mm=depth_or_thickness_mm,
    )
    return {
        "ok": result.get("ok", False),
        "shape": "rectangular_block",
        "parsed": {
            "widthMm": width_mm,
            "heightMm": height_mm,
            "depthMm": depth_or_thickness_mm,
        },
        "result": result,
    }


# ========================================================================
# Skill 增强工具集 — 通过 Python COM 直接调用 SolidWorks
# ========================================================================


def _create_empty_dispatch_variant():
    """创建可传给 COM 接口的空 Dispatch 参数。"""
    return win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)


def _sw_app_summary(doc) -> dict[str, Any]:
    """Return a compact document summary (skill-style)."""
    if doc is None:
        return {"has_document": False}
    try:
        def _safe_get(obj, name):
            member = getattr(obj, name, None)
            if member is None:
                return None
            return member() if callable(member) else member

        return {
            "has_document": True,
            "title": _safe_get(doc, "GetTitle"),
            "path": _safe_get(doc, "GetPathName"),
            "type": _safe_get(doc, "GetType"),
        }
    except Exception:
        return {"has_document": False}


def _sw_component_summary(component) -> dict[str, Any]:
    """Return a compact component summary."""
    try:
        def _safe_get(obj, name):
            member = getattr(obj, name, None)
            if member is None:
                return None
            return member() if callable(member) else member

        return {
            "name": _safe_get(component, "Name2"),
            "path": _safe_get(component, "GetPathName"),
            "suppressed": bool(_safe_get(component, "IsSuppressed") or False),
            "visible": _safe_get(component, "Visible"),
        }
    except Exception:
        return {"name": "unknown"}


def _sw_get(obj, name):
    """获取 COM 属性/方法返回值，兼容属性即方法的情况。"""
    member = getattr(obj, name, None)
    if member is None:
        return None
    return member() if callable(member) else member


def _sw_set_component_fixed(asm_model, component, fixed: bool = True) -> bool:
    """Fix or float an assembly component through the active selection."""
    asm_model.ClearSelection2(True)
    selected = False
    try:
        selected = bool(component.Select4(False, _create_empty_dispatch_variant(), False))
    except Exception:
        selected = False
    if not selected:
        try:
            selected = bool(
                asm_model.Extension.SelectByID2(
                    _sw_get(component, "Name2") or "",
                    "COMPONENT",
                    0, 0, 0, False, 0,
                    _create_empty_dispatch_variant(), 0,
                )
            )
        except Exception:
            selected = False
    if not selected:
        raise RuntimeError(f"Failed to select component: {_sw_get(component, 'Name2')}")
    member_name = "FixComponent" if fixed else "UnfixComponent"
    result = _sw_get(asm_model, member_name)
    asm_model.ClearSelection2(True)
    return bool(result) if result is not None else True


@mcp.tool()
def solidworks_health_check(
    start_solidworks: bool = False,
    check_motion_type_library: bool = True,
) -> dict[str, Any]:
    """检查 SolidWorks 自动化环境的依赖、COM 注册和 Motion 类型库。"""
    _co_initialize()
    try:
        with _sw_global_lock:
            checks = {
                "python_executable": sys.executable,
                "python_version": sys.version.split()[0],
                "platform": platform.platform(),
                "missing_com_dependencies": missing_com_dependencies(),
                "solidworks_detected": solidworks_installed(),
            }
            if check_motion_type_library:
                motion_tlb = ensure_motion_type_library(raise_on_error=False)
                checks["motion_type_library"] = motion_tlb
                checks["motion_type_library_ready"] = bool(motion_tlb)
            if start_solidworks:
                app = _get_app(create=True)
                active_doc = getattr(app, "ActiveDoc", None)
                checks["solidworks_revision"] = _value_or_call(getattr(app, "RevisionNumber", None))
                checks["active_document"] = _sw_app_summary(active_doc)
            issues = []
            if checks.get("missing_com_dependencies"):
                issues.append("Missing Python COM dependencies.")
            if not checks.get("solidworks_detected"):
                issues.append("SolidWorks COM registration not detected.")
            if check_motion_type_library and not checks.get("motion_type_library_ready"):
                issues.append("Motion Study type library not found.")
            return {"status": "ok" if not issues else "warning", "checks": checks, "issues": issues}
    finally:
        _co_uninitialize()


@mcp.tool()
def solidworks_create_basic_part(
    shape: str = "cylinder",
    output_path: str | None = None,
    plane: str = "Front Plane",
    width_mm: float = 80.0,
    height_mm: float = 60.0,
    radius_mm: float = 25.0,
    depth_mm: float = 50.0,
    color: str | None = None,
) -> dict[str, Any]:
    """创建一个简单的圆柱或盒体零件（纯 Python COM，不依赖 Bridge）。"""
    _co_initialize()
    try:
        with _sw_global_lock:
            from sw_connect import new_document as skill_new_doc, find_template
            from sw_part import sketch, sketch_circle, sketch_rectangle, extrude_boss as skill_extrude

            app = _get_app(create=True)
            template = find_template(app, "part")
            model = skill_new_doc(app, "part", template_path=template)
            if model is None:
                return {"ok": False, "reason": "new_document_failed"}

            with sketch(model, plane) as sketch_name:
                if shape == "cylinder":
                    sketch_circle(model, 0.0, 0.0, skill_mm(radius_mm))
                    shape_label = "cylinder"
                else:
                    sketch_rectangle(model, 0.0, 0.0, skill_mm(width_mm), skill_mm(height_mm))
                    shape_label = "box"

            feature = skill_extrude(model, sketch_name, skill_mm(depth_mm))
            appearance_ok = None
            if color:
                appearance_ok = set_document_appearance(model, color)
            save_ok = None
            if output_path:
                from sw_connect import save_document as skill_save
                save_ok = skill_save(model, output_path)
            model.ForceRebuild3(False)
            return {
                "ok": feature is not None,
                "shape": shape_label,
                "feature_created": feature is not None,
                "appearance_ok": appearance_ok,
                "saved": save_ok,
                "output_path": output_path,
                "document": _sw_app_summary(model),
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    finally:
        _co_uninitialize()


@mcp.tool()
def solidworks_add_component_v2(
    path: str,
    x_mm: float = 0.0,
    y_mm: float = 0.0,
    z_mm: float = 0.0,
    config_name: str = "",
    fix_component: bool = False,
) -> dict[str, Any]:
    """向活动装配体添加零部件（增强版，使用 skill 的 AddComponent 逻辑）。"""
    _co_initialize()
    try:
        with _sw_global_lock:
            app = _get_app(create=False)
            if app is None:
                return {"ok": False, "reason": "solidworks_not_running"}
            asm = getattr(app, "ActiveDoc", None)
            if asm is None:
                return {"ok": False, "reason": "no_active_document"}
            if _sw_get(asm, "GetType") != 2:
                return {"ok": False, "reason": "not_an_assembly"}
            component = skill_add_component(asm, path, skill_mm(x_mm), skill_mm(y_mm), skill_mm(z_mm), config_name=config_name, sw=app)
            if component is None:
                return {"ok": False, "reason": "add_component_failed"}
            resolve_component(component)
            fixed = None
            if fix_component:
                fixed = _sw_set_component_fixed(asm, component, fixed=True)
            return {
                "ok": True,
                "component": _sw_component_summary(component),
                "fixed": fixed,
                "component_count": len(get_components(asm) or []),
                "document": _sw_app_summary(asm),
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    finally:
        _co_uninitialize()


@mcp.tool()
def solidworks_set_component_fixed(
    component_keyword: str,
    fixed: bool = True,
) -> dict[str, Any]:
    """按组件名关键字固定或浮动装配体组件。"""
    _co_initialize()
    try:
        with _sw_global_lock:
            app = _get_app(create=False)
            if app is None:
                return {"ok": False, "reason": "solidworks_not_running"}
            asm = getattr(app, "ActiveDoc", None)
            if asm is None or _sw_get(asm, "GetType") != 2:
                return {"ok": False, "reason": "not_an_assembly"}
            component = find_component_by_name(asm, component_keyword)
            ok = _sw_set_component_fixed(asm, component, fixed=fixed)
            return {
                "ok": ok,
                "fixed": fixed,
                "component": _sw_component_summary(component),
                "document": _sw_app_summary(asm),
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    finally:
        _co_uninitialize()


def _plane_aliases(name: str) -> list[str]:
    """返回平面名称的中英文候选列表。"""
    PLANE_MAP = {
        "Front Plane": ["Front Plane", "前视基准面"],
        "Top Plane": ["Top Plane", "上视基准面"],
        "Right Plane": ["Right Plane", "右视基准面"],
        "前视基准面": ["前视基准面", "Front Plane"],
        "上视基准面": ["上视基准面", "Top Plane"],
        "右视基准面": ["右视基准面", "Right Plane"],
    }
    return PLANE_MAP.get(name, [name])


@mcp.tool()
def solidworks_add_coincident_mate(
    component_a_keyword: str,
    component_b_keyword: str,
    feature_a_name: str = "Front Plane",
    feature_b_name: str = "Front Plane",
    mate_name: str | None = None,
) -> dict[str, Any]:
    """在两个组件的指定基准面/特征之间添加重合 Mate。"""
    _co_initialize()
    try:
        with _sw_global_lock:
            app = _get_app(create=False)
            if app is None:
                return {"ok": False, "reason": "solidworks_not_running"}
            asm = getattr(app, "ActiveDoc", None)
            if asm is None or _sw_get(asm, "GetType") != 2:
                return {"ok": False, "reason": "not_an_assembly"}
            component_a = find_component_by_name(asm, component_a_keyword)
            component_b = find_component_by_name(asm, component_b_keyword)
            entity_a = get_component_feature_entity(component_a, _plane_aliases(feature_a_name))
            entity_b = get_component_feature_entity(component_b, _plane_aliases(feature_b_name))
            select_entities_for_mate(asm, entity_a, entity_b, mark=1)
            mate = add_mate5_checked(asm, SW_MATE_COINCIDENT, name=mate_name)
            return {
                "ok": mate is not None,
                "mate_name": mate_name or (_sw_get(mate, "Name") if mate else None),
                "component_a": _sw_get(component_a, "Name2"),
                "component_b": _sw_get(component_b, "Name2"),
                "mate_features": collect_mate_feature_summary(asm),
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    finally:
        _co_uninitialize()


@mcp.tool()
def solidworks_add_distance_mate(
    component_a_keyword: str,
    component_b_keyword: str,
    feature_a_name: str = "Front Plane",
    feature_b_name: str = "Front Plane",
    distance_mm: float = 0.0,
    mate_name: str | None = None,
) -> dict[str, Any]:
    """在两个组件的指定基准面/特征之间添加距离 Mate。"""
    _co_initialize()
    try:
        with _sw_global_lock:
            app = _get_app(create=False)
            if app is None:
                return {"ok": False, "reason": "solidworks_not_running"}
            asm = getattr(app, "ActiveDoc", None)
            if asm is None or _sw_get(asm, "GetType") != 2:
                return {"ok": False, "reason": "not_an_assembly"}
            component_a = find_component_by_name(asm, component_a_keyword)
            component_b = find_component_by_name(asm, component_b_keyword)
            entity_a = get_component_feature_entity(component_a, _plane_aliases(feature_a_name))
            entity_b = get_component_feature_entity(component_b, _plane_aliases(feature_b_name))
            select_entities_for_mate(asm, entity_a, entity_b, mark=1)
            mate = add_mate5_checked(asm, SW_MATE_DISTANCE, distance=skill_mm(distance_mm), name=mate_name)
            return {
                "ok": mate is not None,
                "mate_name": mate_name or (_sw_get(mate, "Name") if mate else None),
                "distance_mm": distance_mm,
                "component_a": _sw_get(component_a, "Name2"),
                "component_b": _sw_get(component_b, "Name2"),
                "mate_features": collect_mate_feature_summary(asm),
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    finally:
        _co_uninitialize()


@mcp.tool()
def solidworks_add_concentric_mate(
    component_a_keyword: str,
    component_b_keyword: str,
    radius_a_min_mm: float = 0.0,
    radius_a_max_mm: float | None = None,
    radius_b_min_mm: float = 0.0,
    radius_b_max_mm: float | None = None,
    lock_rotation: bool = False,
    mate_name: str | None = None,
) -> dict[str, Any]:
    """按圆柱面半径范围添加同心 Mate，可选择是否锁转。"""
    _co_initialize()
    try:
        with _sw_global_lock:
            app = _get_app(create=False)
            if app is None:
                return {"ok": False, "reason": "solidworks_not_running"}
            asm = getattr(app, "ActiveDoc", None)
            if asm is None or _sw_get(asm, "GetType") != 2:
                return {"ok": False, "reason": "not_an_assembly"}
            component_a = find_component_by_name(asm, component_a_keyword)
            component_b = find_component_by_name(asm, component_b_keyword)
            mate = add_concentric_mate_by_cylinders(
                asm, component_a, component_b,
                radius_a=(skill_mm(radius_a_min_mm), skill_mm(radius_a_max_mm) if radius_a_max_mm is not None else None),
                radius_b=(skill_mm(radius_b_min_mm), skill_mm(radius_b_max_mm) if radius_b_max_mm is not None else None),
                name=mate_name, lock_rotation=lock_rotation,
            )
            return {
                "ok": mate is not None,
                "mate_name": mate_name or (_sw_get(mate, "Name") if mate else None),
                "lock_rotation": lock_rotation,
                "component_a": _sw_get(component_a, "Name2"),
                "component_b": _sw_get(component_b, "Name2"),
                "mate_features": collect_mate_feature_summary(asm),
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    finally:
        _co_uninitialize()


@mcp.tool()
def solidworks_set_appearance(
    target: str = "document",
    color: str = "#BFC4C8",
    component_keyword: str | None = None,
) -> dict[str, Any]:
    """设置活动文档或指定组件的外观颜色（支持 #RRGGBB 或 preset 名称）。"""
    _co_initialize()
    try:
        with _sw_global_lock:
            app = _get_app(create=False)
            if app is None:
                return {"ok": False, "reason": "solidworks_not_running"}
            model = getattr(app, "ActiveDoc", None)
            if model is None:
                return {"ok": False, "reason": "no_active_document"}
            if target == "document":
                ok = set_document_appearance(model, color)
                component = None
            elif target == "component":
                if not component_keyword:
                    return {"ok": False, "reason": "component_keyword_required"}
                if _sw_get(model, "GetType") != 2:
                    return {"ok": False, "reason": "not_an_assembly"}
                component = find_component_by_name(model, component_keyword)
                ok = set_component_appearance(component, color)
            else:
                return {"ok": False, "reason": f"unsupported_target: {target}"}
            model.ForceRebuild3(False)
            return {
                "ok": bool(ok),
                "target": target,
                "color": color,
                "component": _sw_component_summary(component) if component else None,
                "document": _sw_app_summary(model),
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    finally:
        _co_uninitialize()


@mcp.tool()
def solidworks_export_active(
    output_path: str,
    export_format: str = "step",
    stl_quality: str = "fine",
) -> dict[str, Any]:
    """导出活动文档为 STEP/STL/IGES/Parasolid/PDF/DXF 格式。"""
    _co_initialize()
    try:
        with _sw_global_lock:
            app = _get_app(create=False)
            if app is None:
                return {"ok": False, "reason": "solidworks_not_running"}
            model = getattr(app, "ActiveDoc", None)
            if model is None:
                return {"ok": False, "reason": "no_active_document"}
            exporters = {
                "step": lambda: export_to_step(model, output_path),
                "stl": lambda: export_to_stl(model, output_path, quality=stl_quality),
                "iges": lambda: export_to_iges(model, output_path),
                "parasolid": lambda: export_to_parasolid(model, output_path),
                "pdf": lambda: export_to_pdf(model, output_path),
                "dxf": lambda: export_to_dxf(model, output_path),
            }
            exporter = exporters.get(export_format)
            if exporter is None:
                return {"ok": False, "reason": f"unsupported_format: {export_format}"}
            success = bool(exporter())
            return {
                "ok": success,
                "output_path": output_path,
                "format": export_format,
                "document": _sw_app_summary(model),
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    finally:
        _co_uninitialize()


@mcp.tool()
def solidworks_review_active(
    output_dir: str,
    basename: str = "mcp_review",
) -> dict[str, Any]:
    """导出多视角 BMP 预览和 JSON 审查报告。"""
    _co_initialize()
    try:
        with _sw_global_lock:
            app = _get_app(create=False)
            if app is None:
                return {"ok": False, "reason": "solidworks_not_running"}
            model = getattr(app, "ActiveDoc", None)
            if model is None:
                return {"ok": False, "reason": "no_active_document"}
            report, report_path = run_review(model, output_dir, basename=basename)
            return {
                "ok": True,
                "report_path": report_path,
                "evaluation": report.get("evaluation"),
                "checks": report.get("checks"),
                "document": _sw_app_summary(model),
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    finally:
        _co_uninitialize()


@mcp.tool()
def solidworks_add_rotary_motor(
    shaft_component_keyword: str,
    rotor_component_keyword: str,
    shaft_radius_min_mm: float = 0.0,
    shaft_radius_max_mm: float | None = None,
    rotor_radius_min_mm: float = 0.0,
    rotor_radius_max_mm: float | None = None,
    rpm: float = 60.0,
    study_name: str = "MCP_旋转马达算例",
    motor_name: str = "MCP_匀速旋转马达",
    duration_seconds: float = 4.0,
    calculate: bool = True,
    play: bool = False,
) -> dict[str, Any]:
    """在活动装配体中新建 Motion Study 并添加匀速旋转马达。"""
    _co_initialize()
    try:
        with _sw_global_lock:
            app = _get_app(create=False)
            if app is None:
                return {"ok": False, "reason": "solidworks_not_running"}
            asm = getattr(app, "ActiveDoc", None)
            if asm is None or _sw_get(asm, "GetType") != 2:
                return {"ok": False, "reason": "not_an_assembly"}
            shaft_comp = find_component_by_name(asm, shaft_component_keyword)
            rotor_comp = find_component_by_name(asm, rotor_component_keyword)
            study = skill_create_motion_study(asm, name=study_name, duration=duration_seconds)
            feature = add_constant_speed_rotary_motor_by_cylinders(
                study, shaft_component=shaft_comp, rotor_component=rotor_comp,
                shaft_radius=(skill_mm(shaft_radius_min_mm), skill_mm(shaft_radius_max_mm) if shaft_radius_max_mm is not None else None),
                rotor_radius=(skill_mm(rotor_radius_min_mm), skill_mm(rotor_radius_max_mm) if rotor_radius_max_mm is not None else None),
                rpm=rpm, name=motor_name,
            )
            calculated = None
            if calculate:
                calculated = calculate_and_play(study, play=play)
            return {
                "ok": feature is not None,
                "study_name": study_name,
                "motor_name": motor_name,
                "motor_feature_created": feature is not None,
                "calculated": calculated,
                "rpm": rpm,
                "shaft_component": _sw_get(shaft_comp, "Name2"),
                "rotor_component": _sw_get(rotor_comp, "Name2"),
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    finally:
        _co_uninitialize()


@mcp.tool()
def solidworks_new_document(
    doc_type: str = "part",
    template_path: str | None = None,
) -> dict[str, Any]:
    """创建一个新的 SolidWorks 零件/装配体/工程图文档（使用 skill 的 NewDocument 逻辑）。"""
    _co_initialize()
    try:
        with _sw_global_lock:
            app = _get_app(create=True)
            from sw_connect import new_document as skill_new_doc
            model = skill_new_doc(app, doc_type, template_path=template_path)
            return {"ok": model is not None, "document": _sw_app_summary(model)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    finally:
        _co_uninitialize()


@mcp.tool()
def solidworks_save_document_v2(path: str | None = None) -> dict[str, Any]:
    """保存活动文档（使用 skill 的 save_document 逻辑，支持自动查找模板路径）。"""
    _co_initialize()
    try:
        with _sw_global_lock:
            app = _get_app(create=False)
            if app is None:
                return {"ok": False, "reason": "solidworks_not_running"}
            model = getattr(app, "ActiveDoc", None)
            if model is None:
                return {"ok": False, "reason": "no_active_document"}
            from sw_connect import save_document as skill_save
            success = skill_save(model, file_path=path)
            return {"ok": bool(success), "path": path or _sw_get(model, "GetPathName"), "document": _sw_app_summary(model)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    finally:
        _co_uninitialize()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
