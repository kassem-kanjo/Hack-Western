"""Geodesy primitives shared by the prism code.

All distances are WGS84 geodesics (not haversine): a vessel track error of a few km would
otherwise change which SAR cells fall inside a prism.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from pyproj import Geod

GEOD = Geod(ellps="WGS84")
KNOT_KM_H = 1.852  # exact by definition of the nautical mile


@dataclass(frozen=True)
class Fix:
    """A position at a UTC instant (an AIS 'off' or 'on' event)."""

    lat: float
    lon: float
    t: datetime


def distance_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Geodesic distance in km."""
    _, _, d = GEOD.inv(a_lon, a_lat, b_lon, b_lat)
    return float(d) / 1000.0


def distance_km_array(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Vectorised geodesic distance in km (broadcasts like numpy)."""
    lat1, lon1, lat2, lon2 = np.broadcast_arrays(
        np.asarray(lat1, float), np.asarray(lon1, float), np.asarray(lat2, float), np.asarray(lon2, float)
    )
    _, _, d = GEOD.inv(lon1.ravel(), lat1.ravel(), lon2.ravel(), lat2.ravel())
    return (np.asarray(d) / 1000.0).reshape(lat1.shape)
