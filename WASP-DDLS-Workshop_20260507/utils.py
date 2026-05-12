"""
utils.py — Shared helpers for the OMERO + Cellpose workshop demo.

Handles BBBC020 image discovery (channel pairing), measurement extraction,
and OMERO connection utilities.
"""

import os
import re
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from skimage.measure import regionprops_table
from tifffile import imread, imwrite


# ---------------------------------------------------------------------------
# BBBC020 image discovery
# ---------------------------------------------------------------------------

# BBBC020 channel naming:  *_c1.TIF = nuclei (Hoechst), *_c5.TIF = cell body (actin)
NUCLEI_PATTERN = re.compile(r"_c1\.TIF$", re.IGNORECASE)
BODY_PATTERN = re.compile(r"_c5\.TIF$", re.IGNORECASE)
COMPOSITE_PATTERN = re.compile(r"_\(c1\+c5\)\.TIF$", re.IGNORECASE)


def discover_image_pairs(data_dir: str | Path) -> list[dict]:
    """Find paired nuclei/body channel TIFs under *data_dir*.

    Returns a list of dicts, each with keys:
        name      — sample identifier (e.g. "jw-15min 1")
        nuclei    — Path to the _c1 file
        body      — Path to the _c5 file
        composite — Path to the _(c1+c5) file (may be None)
        condition — extracted condition string (e.g. "15min", "Kontrolle")
        replicate — replicate number (int or None for controls)
    """
    data_dir = Path(data_dir)
    pairs: dict[str, dict] = {}

    for tif in sorted(data_dir.rglob("*.TIF")):
        name = tif.parent.name  # e.g. "jw-15min 1"
        if name not in pairs:
            pairs[name] = {
                "name": name,
                "nuclei": None,
                "body": None,
                "composite": None,
            }

        if NUCLEI_PATTERN.search(tif.name):
            pairs[name]["nuclei"] = tif
        elif BODY_PATTERN.search(tif.name):
            pairs[name]["body"] = tif
        elif COMPOSITE_PATTERN.search(tif.name):
            pairs[name]["composite"] = tif

    result = []
    for p in pairs.values():
        condition, replicate = _parse_condition(p["name"])
        p["condition"] = condition
        p["replicate"] = replicate
        result.append(p)

    result.sort(key=lambda x: (x["condition"], x["replicate"] or 0))
    return result


def _parse_condition(name: str) -> tuple[str, Optional[int]]:
    """Extract condition and replicate from a folder name like 'jw-15min 3'."""
    m = re.match(r"jw-(\S+?)(\d*)$", name.replace(" ", ""))
    if not m:
        return name, None
    cond = m.group(1).rstrip("0123456789")
    rep_str = name.split()[-1] if " " in name else m.group(2)
    try:
        rep = int(rep_str)
    except (ValueError, TypeError):
        rep = None
    return cond, rep


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------

def load_channels(pair: dict) -> np.ndarray:
    """Load nuclei + body channels and stack to (Y, X, 2) for Cellpose.

    Channel order: [body/cytoplasm, nuclei] matching Cellpose channels=[1, 2].
    """
    nuc = imread(str(pair["nuclei"]))
    body = imread(str(pair["body"]))

    if nuc.ndim == 3:
        nuc = nuc.max(axis=-1) if nuc.shape[2] <= 4 else nuc[0]
    if body.ndim == 3:
        body = body.max(axis=-1) if body.shape[2] <= 4 else body[0]

    return np.stack([body, nuc], axis=-1)


# ---------------------------------------------------------------------------
# Measurement extraction
# ---------------------------------------------------------------------------

def extract_measurements(masks: np.ndarray,
                         intensity_image: np.ndarray) -> pd.DataFrame:
    """Extract per-cell measurements from a label mask."""
    if intensity_image.ndim == 3:
        intensity_image = intensity_image.max(axis=-1)

    props = regionprops_table(
        masks,
        intensity_image=intensity_image,
        properties=[
            "label", "area", "centroid",
            "mean_intensity", "eccentricity",
        ],
    )
    return pd.DataFrame(props)


# ---------------------------------------------------------------------------
# Result I/O
# ---------------------------------------------------------------------------

def save_results(masks: np.ndarray,
                 measurements: pd.DataFrame,
                 out_dir: str | Path,
                 stem: str) -> tuple[Path, Path]:
    """Save mask TIFF and measurements CSV. Returns (mask_path, csv_path)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    mask_path = out_dir / f"{stem}_masks.tiff"
    csv_path = out_dir / f"{stem}_measurements.csv"

    imwrite(str(mask_path), masks.astype(np.uint16))
    measurements.to_csv(csv_path, index=False)

    return mask_path, csv_path


# ---------------------------------------------------------------------------
# OMERO helpers
# ---------------------------------------------------------------------------

def omero_connect(host: str = "omero.scilifelab.se",
                  port: int = 4064,
                  username: str | None = None,
                  password: str | None = None):
    """Connect to OMERO and return a BlitzGateway (caller must close it)."""
    import getpass
    from omero.gateway import BlitzGateway

    if username is None:
        username = os.environ.get("OMERO_USER") or input("OMERO username: ")
    if password is None:
        password = os.environ.get("OMERO_PASSWORD") or getpass.getpass("OMERO password: ")

    conn = BlitzGateway(username, password, host=host, port=port, secure=True)
    if not conn.connect():
        raise ConnectionError(f"Could not connect to OMERO at {host}:{port}")
    print(f"Connected to OMERO as {username}")
    return conn
