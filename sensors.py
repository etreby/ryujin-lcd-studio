#!/usr/bin/env python3
"""Read-only Linux host sensor discovery and sampling for Ryujin LCD Studio."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

SYS = Path("/sys")
PROC = Path("/proc")
PYTHON = Path.home() / ".local/share/ryujin-lcd-studio/venv/bin/python"
APP_DIR = Path(__file__).resolve().parent


def _stable_hwmon_key(path: Path, root: Path) -> str:
    """Use the canonical device path, with volatile hwmonN components removed."""
    try:
        resolved = path.resolve(strict=True)
        base = root.resolve()
        rel = str(resolved.relative_to(base))
    except (OSError, ValueError):
        rel = path.name
    rel = re.sub(r"(^|/)hwmon\d+(?=/|$)", r"\1hwmon", rel)
    try:
        name = (path / "name").read_text().strip() if (path / "name").is_file() else path.name
    except OSError:
        name = path.name
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).strip("-").lower() or "sensor"
    digest = hashlib.sha1(f"{rel}/{name}".encode()).hexdigest()[:10]
    return f"hwmon.{slug}.{digest}"


def _nvidia_rows(timeout=0.8):
    args = ["nvidia-smi", "--query-gpu=uuid,name,temperature.gpu,utilization.gpu,memory.total,memory.used,memory.free,clocks.gr,clocks.mem,power.draw,fan.speed", "--format=csv,noheader,nounits"]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in result.stdout.splitlines():
        fields = [part.strip() for part in line.split(",")]
        if len(fields) != 11 or not fields[0]:
            continue
        rows.append(dict(zip(("uuid", "name", "temperature", "utilization", "memory_total", "memory_used", "memory_free", "clock_graphics", "clock_memory", "power", "fan"), fields)))
    return rows


def _mem_values(proc_root=PROC):
    values = {}
    try:
        for line in (Path(proc_root) / "meminfo").read_text().splitlines():
            match = re.match(r"^(\w+):\s+(\d+)\s+kB", line)
            if match:
                values[match.group(1)] = int(match.group(2)) * 1024
    except OSError:
        return {}
    return values


def discover(sys_root=SYS, proc_root=PROC, include_gpus=True):
    items = [
        {"id": "cpu.usage", "label": "CPU usage", "unit": "%", "kind": "cpu_usage"},
        {"id": "cpu.frequency", "label": "CPU frequency", "unit": "MHz", "kind": "cpu_frequency"},
    ]
    mem = _mem_values(proc_root)
    if mem:
        items += [
            {"id": "memory.free", "label": "Memory free", "unit": "GB", "kind": "memory", "key": "MemFree"},
            {"id": "memory.available", "label": "Memory available", "unit": "GB", "kind": "memory", "key": "MemAvailable"},
            {"id": "memory.used", "label": "Memory used", "unit": "GB", "kind": "memory", "key": "MemUsed"},
            {"id": "memory.total", "label": "Memory total", "unit": "GB", "kind": "memory", "key": "MemTotal"},
            {"id": "memory.usage", "label": "Memory usage", "unit": "%", "kind": "memory", "key": "MemPercent"},
        ]
        if "SwapTotal" in mem:
            items += [{"id": f"swap.{k.lower()}", "label": f"Swap {k.lower()}", "unit": "%" if k == "usage" else "GB", "kind": "swap", "key": k} for k in ("used", "free", "total", "usage")]
    hwroot = Path(sys_root) / "class/hwmon"
    for hw in sorted(hwroot.glob("hwmon*")):
        key = _stable_hwmon_key(hw, Path(sys_root))
        try:
            device = (hw / "name").read_text(errors="replace").strip() if (hw / "name").is_file() else hw.name
        except OSError:
            device = hw.name
        for stem, kind, unit, label in (("temp", "temp", "°C", "temperature"), ("fan", "fan", "rpm", "fan"), ("in", "voltage", "V", "voltage"), ("curr", "current", "A", "current"), ("power", "power", "W", "power")):
            for inp in sorted(hw.glob(f"{stem}*_input")):
                idx = inp.name[len(stem):-len("_input")]
                labelpath = hw / f"{stem}{idx}_label"
                try:
                    channel = labelpath.read_text(errors="replace").strip() if labelpath.is_file() else f"{label} {idx}"
                except OSError:
                    channel = f"{label} {idx}"
                ident = f"{key}.{kind}.{idx}"
                scale = {"temp":1000,"voltage":1000,"current":1000,"power":1000000}.get(kind,1)
                prefix = "CPU temperature" if device.lower().startswith("coretemp") and kind == "temp" else "DIMM temperature" if device.lower().startswith("spd5118") and kind == "temp" else "SSD temperature" if device.lower() == "nvme" and kind == "temp" else ""
                label_text = " ".join(x for x in (prefix, device, channel) if x)
                items.append({"id": ident, "label": label_text, "unit": unit, "kind": "hwmon", "path": str(inp), "scale": scale})
    if include_gpus:
        gpu_rows = sorted(_nvidia_rows(), key=lambda row: row["uuid"])
        for ordinal, row in enumerate(gpu_rows, 1):
            uuid = row["uuid"]
            for field, label, unit in (("temperature", "temperature", "°C"), ("utilization", "utilization", "%"), ("memory_used", "memory used", "MiB"), ("memory_free", "memory free", "MiB"), ("memory_total", "memory total", "MiB"), ("clock_graphics", "graphics clock", "MHz"), ("clock_memory", "memory clock", "MHz"), ("power", "power", "W"), ("fan", "fan", "%")):
                if row[field] not in ("N/A", "[Not Supported]", ""):
                    items.append({"id": f"gpu.{uuid}.{field}", "label": f"GPU {ordinal} · {row['name']} ({uuid[-8:]}) {label}", "unit": unit, "kind": "gpu", "uuid": uuid, "field": field})
    return items


def _cpu_usage(proc_root=PROC):
    def read():
        fields = (Path(proc_root) / "stat").read_text().splitlines()[0].split()[1:]
        values = [int(x) for x in fields]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values[:8]), idle
    try:
        a = read(); time.sleep(0.1); b = read()
        total, idle = b[0] - a[0], b[1] - a[1]
        return round(100 * (total - idle) / total, 1) if total > 0 else None
    except (OSError, ValueError, IndexError):
        return None


def _gpu_sample(uuid, field):
    try:
        rows = _nvidia_rows()
    except subprocess.SubprocessError:
        return None
    row = next((r for r in rows if r["uuid"] == uuid), None)
    if not row:
        return None
    raw = row.get(field, "N/A")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def read_sensor(sensor_id, *, sys_root=SYS, proc_root=PROC):
    if sensor_id.startswith("gpu."):
        match = re.fullmatch(r"gpu\.(GPU-[A-Za-z0-9-]+)\.(temperature|utilization|memory_used|memory_free|memory_total|clock_graphics|clock_memory|power|fan)", sensor_id)
        if not match:
            return None
        return _gpu_sample(match.group(1), match.group(2))
    entry = next((x for x in discover(sys_root, proc_root, include_gpus=False) if x["id"] == sensor_id), None)
    if not entry:
        return None
    kind = entry["kind"]
    try:
        if kind == "cpu_usage":
            value = _cpu_usage(proc_root)
        elif kind == "cpu_frequency":
            path = Path(sys_root) / "devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
            value = round(int(path.read_text().strip()) / 1000, 1)
        elif kind in ("memory", "swap"):
            m = _mem_values(proc_root)
            total = m.get("MemTotal" if kind == "memory" else "SwapTotal", 0)
            free = m.get("MemAvailable" if kind == "memory" else "SwapFree", 0) if entry["key"] in ("MemUsed", "used", "MemPercent", "usage") else m.get("MemFree" if kind == "memory" else "SwapFree", 0)
            used = max(0, total - free)
            if entry["key"] in ("MemUsed", "used"): value = round(used / 1e9, 2)
            elif entry["key"] in ("MemFree", "free"): value = round(free / 1e9, 2)
            elif entry["key"] in ("MemAvailable",): value = round(m.get("MemAvailable", 0) / 1e9, 2)
            elif entry["key"] in ("MemTotal", "total"): value = round(total / 1e9, 2)
            else: value = round(100 * used / total, 1) if total else None
        elif kind == "hwmon":
            value = int(Path(entry["path"]).read_text().strip()) / entry["scale"]
            value = round(value, 1 if entry["unit"] != "rpm" else 0)
        elif kind == "gpu":
            value = _gpu_sample(entry["uuid"], entry["field"])
        else:
            value = None
    except (OSError, ValueError, TypeError, ZeroDivisionError):
        return None
    return value if value is not None and math.isfinite(value) else None


def read_sensors(sensor_ids, *, sys_root=SYS, proc_root=PROC):
    """Sample a batch of recognized sensors with at most one GPU query."""
    ids = list(dict.fromkeys(sensor_ids))
    result = {sensor_id: None for sensor_id in ids}
    base = {item["id"]: item for item in discover(sys_root, proc_root, include_gpus=False)}
    gpu_ids = {}
    for sensor_id in ids:
        match = re.fullmatch(r"gpu\.(GPU-[A-Za-z0-9-]+)\.(temperature|utilization|memory_used|memory_free|memory_total|clock_graphics|clock_memory|power|fan)", sensor_id)
        if match:
            gpu_ids[sensor_id] = match.groups()
    rows = _nvidia_rows() if gpu_ids else []
    rows_by_uuid = {row["uuid"]: row for row in rows}
    for sensor_id in ids:
        if sensor_id in gpu_ids:
            uuid, field = gpu_ids[sensor_id]
            try:
                raw = rows_by_uuid[uuid][field]
                value = float(raw)
                result[sensor_id] = value if math.isfinite(value) else None
            except (KeyError, TypeError, ValueError):
                pass
            continue
        if sensor_id not in base:
            continue
        # Reuse the already discovered entry so hwmon discovery and memory
        # metadata are not repeated for each selected card statistic.
        item = base[sensor_id]
        try:
            if item["kind"] == "cpu_usage":
                value = _cpu_usage(proc_root)
            else:
                value = read_sensor(sensor_id, sys_root=sys_root, proc_root=proc_root)
            result[sensor_id] = value
        except (OSError, ValueError, TypeError, ZeroDivisionError):
            result[sensor_id] = None
    return result


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true")
    group.add_argument("--read", metavar="SENSOR_ID")
    args = parser.parse_args()
    if args.list:
        print(json.dumps(discover(), ensure_ascii=False))
    else:
        value = read_sensor(args.read)
        print("N/A" if value is None else f"{value:g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
