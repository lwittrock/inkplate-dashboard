"""Settings, read from the environment.

On the server, systemd loads /etc/inkplate-screen.env. On the laptop, put the
same KEY=value lines in server/local.env (gitignored); load_env_file() reads it.
Defaults mirror config.h.example; the location defaults to Delft, so set
LATITUDE and LONGITUDE to the real place.
"""

import os
from dataclasses import dataclass
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent


def load_env_file(path: Path = SERVER_DIR / "local.env") -> None:
    """Add KEY=value lines to os.environ without overriding what is already set."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    latitude: float
    longitude: float
    ns_api_key: str
    station_central: str
    station_hs: str
    station_destination: str
    buienradar_stale_min: int
    buienradar_consensus_km: float
    buienradar_max_candidates: int
    show_version_footer: bool
    screen_format: str          # "grey" or "mono": what to send a device that can draw both
    crisp_small: bool           # greyscale: small text without anti-aliasing (SMALL_TEXT=crisp)

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            latitude=float(e("LATITUDE", "52.0705")),
            longitude=float(e("LONGITUDE", "4.3007")),
            ns_api_key=e("NS_API_KEY", ""),
            station_central=e("STATION_CODE_CENTRAL", "GVC"),
            station_hs=e("STATION_CODE_HS", "GV"),
            station_destination=e("STATION_CODE_DESTINATION", "TBU"),
            buienradar_stale_min=int(e("BUIENRADAR_STALE_MIN", "60")),
            buienradar_consensus_km=float(e("BUIENRADAR_CONSENSUS_KM", "30")),
            buienradar_max_candidates=int(e("BUIENRADAR_MAX_CANDIDATES", "6")),
            show_version_footer=e("SHOW_VERSION_FOOTER", "0") == "1",
            screen_format="mono" if e("SCREEN_FORMAT", "grey").strip().lower() == "mono" else "grey",
            crisp_small=e("SMALL_TEXT", "smooth").strip().lower() == "crisp",
        )
