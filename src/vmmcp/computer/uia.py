"""uia.py — accessibility tree as plain data, for models that can't (or shouldn't) rely on pixels.

Runs a PowerShell UI-Automation walk of one window.  Must run on the interactive desktop,
which is why it lives in the agent.  Returns controls with names, types and click centres.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

PS_SCRIPT = r"""
param([long]$Hwnd = 0, [int]$MaxDepth = 12, [int]$MaxItems = 250, [int]$MaxSeconds = 12)
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$AE = [System.Windows.Automation.AutomationElement]
$root = if ($Hwnd -ne 0) { $AE::FromHandle([IntPtr]$Hwnd) } else { $AE::RootElement }
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$interactive = 'Edit','Button','CheckBox','RadioButton','ComboBox','ListItem','MenuItem','TabItem','Hyperlink','Slider','Document','TreeItem','SplitButton'
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$out = New-Object System.Collections.Generic.List[object]
$q = New-Object System.Collections.Generic.Queue[object]
$q.Enqueue(@($root, 0))
while ($q.Count -gt 0 -and $out.Count -lt $MaxItems -and $sw.Elapsed.TotalSeconds -lt $MaxSeconds) {
  $it = $q.Dequeue(); $el = $it[0]; $d = $it[1]
  $c = $null; try { $c = $el.Current } catch {}
  if ($c -ne $null) {
    $r = $c.BoundingRectangle
    if (-not $c.IsOffscreen -and -not $r.IsEmpty -and $r.Width -gt 0 -and $r.Height -gt 0) {
      $type = ($c.ControlType.ProgrammaticName -replace '^ControlType\.', '')
      $name = $c.Name
      if ($name -or ($interactive -contains $type)) {
        $out.Add([pscustomobject]@{ name = $name; type = $type; x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height; enabled = $c.IsEnabled; focused = $c.HasKeyboardFocus; id = $c.AutomationId })
      }
    }
  }
  if ($d -lt $MaxDepth) {
    $ch = $null; try { $ch = $walker.GetFirstChild($el) } catch {}
    while ($ch -ne $null) { $q.Enqueue(@($ch, $d + 1)); try { $ch = $walker.GetNextSibling($ch) } catch { $ch = $null } }
  }
}
ConvertTo-Json -InputObject @($out) -Compress -Depth 3
"""


def tree(hwnd: int = 0, max_items: int = 250, timeout: int = 20) -> list[dict]:
    fd, path = tempfile.mkstemp(suffix=".ps1")
    with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
        f.write(PS_SCRIPT)
    pwsh = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    try:
        r = subprocess.run(
            [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path,
             "-Hwnd", str(hwnd), "-MaxItems", str(max_items)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=0x08000000)       # CREATE_NO_WINDOW
    finally:
        os.unlink(path)
    text = r.stdout.strip()
    if not text:
        raise RuntimeError(f"UI Automation returned nothing. {r.stderr.strip()[:300]}")
    data = json.loads(text)
    return data if isinstance(data, list) else [data]
