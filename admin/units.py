#!/usr/bin/env python3
"""Standardized units and formatting for MacServer.

All sizes use SI decimal units (KB=1000, MB=1_000_000, GB=1_000_000_000)
to match network/disk specs and user expectations.
"""

SIZE_UNITS = [
    (1_000_000_000_000, "TB"),
    (1_000_000_000, "GB"),
    (1_000_000, "MB"),
    (1_000, "KB"),
    (1, "B"),
]

RATE_UNITS = [
    (1_000_000_000, "GB/s"),
    (1_000_000, "MB/s"),
    (1_000, "KB/s"),
    (1, "B/s"),
]

TEMP_UNITS = "°C"
FREQ_UNITS = "MHz"
PERCENT_UNITS = "%"
RPM_UNITS = "RPM"


def fmt_bytes(n, rate=False):
    """Format byte count with SI decimal units.
    
    Args:
        n: bytes (int or float)
        rate: if True, append '/s' for rates
    Returns:
        Formatted string like "1.2 MB" or "500 KB/s"
    """
    if not isinstance(n, (int, float)) or n != n:  # NaN check
        return "?"
    if n < 0:
        n = 0
    units = RATE_UNITS if rate else SIZE_UNITS
    for divisor, unit in units:
        if n >= divisor or divisor == 1:
            val = n / divisor
            # Show 1 decimal for values < 100 in non-base unit
            if divisor > 1 and val < 100:
                return f"{val:.1f} {unit}"
            return f"{val:.0f} {unit}"
    return "0 B"


def fmt_temp(c):
    """Format temperature in Celsius."""
    if c is None:
        return "?"
    return f"{c:.0f}{TEMP_UNITS}"


def fmt_freq(mhz):
    """Format frequency in MHz."""
    if mhz is None:
        return "?"
    if mhz >= 1000:
        return f"{mhz / 1000:.1f} GHz"
    return f"{mhz:.0f} {FREQ_UNITS}"


def fmt_pct(pct):
    """Format percentage."""
    if pct is None:
        return "?"
    return f"{pct:.1f}{PERCENT_UNITS}"


def fmt_rpm(rpm):
    """Format fan RPM."""
    if rpm is None:
        return "?"
    return f"{rpm:.0f} {RPM_UNITS}"


def parse_size(text):
    """Parse human-readable size string to bytes.
    
    Supports: B, KB, MB, GB, TB (decimal) and KiB, MiB, GiB, TiB (binary)
    """
    import re
    m = re.match(r"\s*([\d.]+)\s*([a-zA-Z]+)", text or "")
    if not m:
        return 0
    val = float(m.group(1))
    unit = m.group(2).upper()
    multipliers = {
        "B": 1, "KB": 1_000, "MB": 1_000_000, "GB": 1_000_000_000, "TB": 1_000_000_000_000,
        "KIB": 1_024, "MIB": 1_024**2, "GIB": 1_024**3, "TIB": 1_024**4,
    }
    return int(val * multipliers.get(unit, 1))


def fmt_duration(seconds):
    """Format duration in human-readable form."""
    if seconds is None:
        return "?"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


def fmt_uptime(seconds):
    """Format uptime compactly."""
    if seconds is None:
        return "?"
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h or d:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


if __name__ == "__main__":
    # Quick tests
    test_values = [0, 500, 1024, 1500, 1_500_000, 2_500_000_000, 3_000_000_000_000]
    print("Size formatting:")
    for v in test_values:
        print(f"  {v:>15} -> {fmt_bytes(v)}")
    print("\nRate formatting:")
    for v in [100, 1500, 2_000_000, 5_000_000_000]:
        print(f"  {v:>15} -> {fmt_bytes(v, rate=True)}")
    print("\nParse test:")
    for s in ["1.5 GB", "500 MB", "2 GiB", "100 KiB"]:
        print(f"  {s:>10} -> {parse_size(s)} bytes")