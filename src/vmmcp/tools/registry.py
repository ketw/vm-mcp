"""tools/registry.py — Windows registry."""

from __future__ import annotations

import base64
import winreg
from typing import Any
from typing import Optional
from ..mcp_instance import mcp


_REG_ROOTS = {
    "HKLM": winreg.HKEY_LOCAL_MACHINE,
    "HKCU": winreg.HKEY_CURRENT_USER,
    "HKCR": winreg.HKEY_CLASSES_ROOT,
    "HKU":  winreg.HKEY_USERS,
    "HKCC": winreg.HKEY_CURRENT_CONFIG,
}
_REG_TYPE_MAP = {
    "REG_SZ":        winreg.REG_SZ,
    "REG_DWORD":     winreg.REG_DWORD,
    "REG_QWORD":     winreg.REG_QWORD,
    "REG_EXPAND_SZ": winreg.REG_EXPAND_SZ,
    "REG_BINARY":    winreg.REG_BINARY,
    "REG_MULTI_SZ":  winreg.REG_MULTI_SZ,
}
_REG_TYPE_NAMES = {v: k for k, v in _REG_TYPE_MAP.items()}

def _split_reg(full_path: str):
    parts = full_path.replace("/", "\\").split("\\", 1)
    root  = _REG_ROOTS.get(parts[0].upper())
    if root is None:
        raise ValueError(f"Unknown registry root: {parts[0]!r}")
    return root, parts[1] if len(parts) > 1 else ""


@mcp.tool()
def registry_read(path: str, value_name: str = "") -> dict:
    """
    Read a registry value.

    Args:
        path: Registry path, e.g. HKLM\\SOFTWARE\\Nemo
        value_name: Value name (empty = default value).

    Returns: {data, type, path, value_name}
    """
    try:
        root, sub = _split_reg(path)
        with winreg.OpenKey(root, sub, access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            data, rt = winreg.QueryValueEx(k, value_name)
        return {
            "data":       data if not isinstance(data, bytes) else base64.b64encode(data).decode(),
            "type":       _REG_TYPE_NAMES.get(rt, str(rt)),
            "path":       path,
            "value_name": value_name or "(default)",
        }
    except FileNotFoundError:
        return {"error": f"Not found: {path}\\{value_name}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def registry_write(path: str, value_name: str, data: Any, reg_type: str = "REG_SZ") -> dict:
    """
    Write a registry value.

    Args:
        path: Registry path.
        value_name: Value name.
        data: Data to write (use base64 string for REG_BINARY).
        reg_type: REG_SZ | REG_DWORD | REG_QWORD | REG_EXPAND_SZ | REG_BINARY | REG_MULTI_SZ.

    Returns: {success, path, value_name}
    """
    try:
        root, sub = _split_reg(path)
        wtype = _REG_TYPE_MAP.get(reg_type.upper(), winreg.REG_SZ)
        if reg_type.upper() == "REG_DWORD":  data = int(data)
        elif reg_type.upper() == "REG_BINARY": data = base64.b64decode(data)
        with winreg.CreateKeyEx(root, sub, access=winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as k:
            winreg.SetValueEx(k, value_name, 0, wtype, data)
        return {"success": True, "path": path, "value_name": value_name}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def registry_delete(path: str, value_name: Optional[str] = None) -> dict:
    """
    Delete a registry value or entire key.

    Args:
        path: Registry path.
        value_name: If provided, delete just this value. If None, delete the whole key.

    Returns: {deleted}
    """
    try:
        root, sub = _split_reg(path)
        if value_name is not None:
            with winreg.OpenKey(root, sub, access=winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as k:
                winreg.DeleteValue(k, value_name)
        else:
            winreg.DeleteKey(root, sub)
        return {"deleted": True}
    except FileNotFoundError:
        return {"error": f"Not found: {path}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def registry_list(path: str) -> dict:
    """
    List subkeys and values in a registry key.

    Args:
        path: Registry path.

    Returns: {subkeys, values, count_subkeys, count_values}
    """
    try:
        root, sub = _split_reg(path)
        with winreg.OpenKey(root, sub, access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            info = winreg.QueryInfoKey(k)
            subkeys = [winreg.EnumKey(k, i) for i in range(info[0])]
            values  = []
            for i in range(info[1]):
                name, data, rt = winreg.EnumValue(k, i)
                values.append({
                    "name": name,
                    "type": _REG_TYPE_NAMES.get(rt, str(rt)),
                    "data": data if not isinstance(data, bytes) else base64.b64encode(data).decode(),
                })
        return {"subkeys": subkeys, "values": values, "count_subkeys": len(subkeys), "count_values": len(values)}
    except FileNotFoundError:
        return {"error": f"Not found: {path}"}
    except Exception as e:
        return {"error": str(e)}
