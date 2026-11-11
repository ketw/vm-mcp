"""tools/network.py — Networking & firewall."""

from __future__ import annotations

import httpx
import json
import socket
import subprocess
from typing import Optional
from ..mcp_instance import mcp
from ..winutil import run_ps


@mcp.tool()
def net_info() -> dict:
    """
    Return network configuration: adapters, IPs, default gateway, DNS, public IP.
    """
    r = run_ps(
        "$adapters = Get-NetIPConfiguration -ErrorAction SilentlyContinue; "
        "$out = @(); "
        "foreach ($a in $adapters) { "
        "  $ipv4 = ($a.IPv4Address | ForEach-Object { $_.IPAddress }) -join ','; "
        "  $dns  = ($a.DNSServer   | ForEach-Object { $_.ServerAddresses } | Where-Object { $_ -ne '' }) -join ','; "
        "  $out += [PSCustomObject]@{ "
        "    Interface = $a.InterfaceAlias; "
        "    IPv4      = $ipv4; "
        "    DNS       = $dns "
        "  } "
        "}; "
        "$out | ConvertTo-Json -Compress"
    )
    public_ip = ""
    try:
        public_ip = httpx.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        pass
    gw = run_ps(
        "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue "
        "| Sort-Object RouteMetric | Select-Object -First 1).NextHop"
    )
    try:
        adapters = json.loads(r["stdout"])
        if isinstance(adapters, dict): adapters = [adapters]
    except Exception:
        adapters = []
    return {
        "adapters":  adapters,
        "public_ip": public_ip,
        "gateway":   gw["stdout"].strip(),
        "hostname":  socket.gethostname(),
    }


@mcp.tool()
def net_ping(host: str, count: int = 4, timeout_ms: int = 1000) -> dict:
    """
    Ping a host and return latency statistics.

    Args:
        host: Hostname or IP address.
        count: Number of ping packets (default 4).
        timeout_ms: Timeout per ping in milliseconds.

    Returns: {host, sent, received, lost_pct, min_ms, max_ms, avg_ms}
    """
    # Use ping.exe directly — Test-Connection parameter set varies across PS versions
    timeout_s = max(1, timeout_ms // 1000)
    result = subprocess.run(
        [r"C:\Windows\System32\ping.exe", "-n", str(count), "-w", str(timeout_ms), host],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s * count + 5,
    )
    out = result.stdout
    import re
    times = [int(m) for m in re.findall(r"time[=<](\d+)ms", out, re.IGNORECASE)]
    sent_m  = re.search(r"Sent\s*=\s*(\d+)", out)
    recv_m  = re.search(r"Received\s*=\s*(\d+)", out)
    sent = int(sent_m.group(1)) if sent_m else count
    recv = int(recv_m.group(1)) if recv_m else len(times)
    return {
        "host":     host,
        "sent":     sent,
        "received": recv,
        "lost_pct": round((sent - recv) / sent * 100) if sent else 100,
        "min_ms":   min(times) if times else None,
        "max_ms":   max(times) if times else None,
        "avg_ms":   round(sum(times) / len(times), 1) if times else None,
        "reachable": recv > 0,
    }


@mcp.tool()
def net_port_scan(
    host: str,
    ports: list[int],
    timeout_ms: int = 500,
) -> dict:
    """
    TCP port scan on a host.

    Args:
        host: Target hostname or IP.
        ports: List of port numbers to test (e.g. [22, 80, 443, 3389]).
        timeout_ms: Connection timeout per port in milliseconds.

    Returns: {host, open_ports, closed_ports, results}
    """
    timeout = timeout_ms / 1000
    open_ports, closed_ports = [], []
    results = []
    for port in ports:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                open_ports.append(port)
                results.append({"port": port, "status": "open"})
        except (ConnectionRefusedError, socket.timeout, OSError):
            closed_ports.append(port)
            results.append({"port": port, "status": "closed"})
    return {
        "host":         host,
        "open_ports":   open_ports,
        "closed_ports": closed_ports,
        "results":      results,
        "scanned":      len(ports),
    }


@mcp.tool()
def net_connections(filter_state: str = "") -> dict:
    """
    List active TCP connections and UDP listeners.

    Args:
        filter_state: Optional TCP state filter — ESTABLISHED, LISTEN, TIME_WAIT, etc.

    Returns: {connections, count}
    """
    cmd = "Get-NetTCPConnection"
    if filter_state:
        cmd += f" -State '{filter_state}'"
    cmd += (" | Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort,"
            "State,OwningProcess | Sort-Object State | ConvertTo-Json -Compress")
    r = run_ps(cmd, timeout=20)
    try:
        conns = json.loads(r["stdout"])
        if isinstance(conns, dict): conns = [conns]
        return {"connections": conns, "count": len(conns)}
    except Exception:
        return {"raw": r["stdout"], "error": r["stderr"]}


@mcp.tool()
def firewall_rule_list(filter_name: str = "", direction: str = "") -> dict:
    """
    List Windows Firewall rules.

    Args:
        filter_name: Optional display name substring filter.
        direction: Optional direction filter — Inbound or Outbound.

    Returns: {rules, count}
    """
    cmd = "Get-NetFirewallRule"
    if filter_name: cmd += f" -DisplayName '*{filter_name}*'"
    if direction:   cmd += f" -Direction {direction}"
    cmd += (" | Select-Object DisplayName,Direction,Action,Enabled,Profile "
            "| ConvertTo-Json -Compress")
    r = run_ps(cmd, timeout=30)
    try:
        rules = json.loads(r["stdout"])
        if isinstance(rules, dict): rules = [rules]
        return {"rules": rules, "count": len(rules)}
    except Exception:
        return {"raw": r["stdout"], "error": r["stderr"]}


@mcp.tool()
def firewall_rule_add(
    name: str,
    direction: str = "Inbound",
    action: str = "Allow",
    protocol: str = "TCP",
    local_port: Optional[int] = None,
    remote_address: str = "Any",
    profile: str = "Any",
    description: str = "",
) -> dict:
    """
    Add a Windows Firewall rule.

    Args:
        name: Display name for the rule.
        direction: Inbound (default) or Outbound.
        action: Allow (default) or Block.
        protocol: TCP (default), UDP, or Any.
        local_port: Local port number (None = any port).
        remote_address: Remote IP/CIDR (default Any).
        profile: Any (default), Domain, Private, or Public.
        description: Optional rule description.

    Returns: {success, name, stderr}
    """
    cmd = (
        f"New-NetFirewallRule -DisplayName '{name}' "
        f"-Direction {direction} -Action {action} -Protocol {protocol} "
        f"-RemoteAddress {remote_address} -Profile {profile}"
    )
    if local_port:
        cmd += f" -LocalPort {local_port}"
    if description:
        cmd += f" -Description '{description.replace(chr(39), chr(39)*2)}'"
    r = run_ps(cmd)
    return {"success": r["exit_code"] == 0, "name": name, "stderr": r["stderr"]}


@mcp.tool()
def firewall_rule_remove(name: str) -> dict:
    """Remove a Windows Firewall rule by display name."""
    r = run_ps(f"Remove-NetFirewallRule -DisplayName '{name}' -ErrorAction SilentlyContinue")
    return {"success": r["exit_code"] == 0, "name": name, "stderr": r["stderr"]}
