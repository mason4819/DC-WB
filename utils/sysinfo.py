"""Small helpers about this process."""
from typing import Optional


def memory_mb() -> Optional[float]:
    """Resident memory of this process in MB (Linux). None where /proc is not available."""
    try:
        with open("/proc/self/status", encoding="utf-8") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (OSError, ValueError):
        pass
    return None
