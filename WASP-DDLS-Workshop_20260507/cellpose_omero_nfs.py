#!/usr/bin/env python
"""
cellpose_omero_nfs.py — GPU segmentation reading images from OMERO NFS mount.

Queries an OMERO Dataset by ID, resolves file paths on the /omero/ NFS mount,
and runs Cellpose segmentation on paired nuclei/body channel images.

Usage:
    python cellpose_omero_nfs.py --dataset-id 362 --output-dir /path/to/output
"""

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
from cellpose import models
from tifffile import imread, imwrite

from utils import extract_measurements, omero_connect, save_results


DEFAULT_OMERO_BASE = Path("/omero")


def parse_args():
    p = argparse.ArgumentParser(
        description="Cellpose segmentation from OMERO NFS mount")
    p.add_argument("--dataset-id", type=int, required=True,
                   help="OMERO Dataset ID to process")
    p.add_argument("--omero-base", type=Path, default=DEFAULT_OMERO_BASE,
                   help="NFS mount root (default: /omero)")
    p.add_argument("--output-dir", type=Path,
                   default=Path("results") / "omero_nfs",
                   help="Where to write masks and CSVs")
    p.add_argument("--model", default="cyto3",
                   help="Cellpose model type (default: cyto3)")
    p.add_argument("--no-gpu", action="store_true",
                   help="Disable GPU")
    p.add_argument("--host", default="omero.scilifelab.se")
    p.add_argument("--port", type=int, default=4064)
    return p.parse_args()


def resolve_nfs_paths(dataset_id: int, omero_base: Path,
                      host: str, port: int) -> list[Path]:
    """Query OMERO for image Fileset paths and map them to local NFS paths."""
    conn = omero_connect(host=host, port=port)
    try:
        dataset = conn.getObject("Dataset", dataset_id)
        if dataset is None:
            raise SystemExit(
                f"ERROR: Dataset {dataset_id} not found in OMERO")

        nfs_root = omero_base
        paths = []
        for image in dataset.listChildren():
            fileset = image.getFileset()
            if fileset:
                for f in fileset.listFiles():
                    rel_path = f.getPath() + f.getName()
                    paths.append(nfs_root / rel_path)
            else:
                print(f"  WARNING: {image.getName()} has no Fileset, skipping")

        print(f"Resolved {len(paths)} files from Dataset "
              f"'{dataset.getName()}' (id={dataset_id})")
        return sorted(paths)
    finally:
        conn.close()


def pair_images(tif_paths: list[Path]) -> list[dict]:
    """Pair _c1 (nuclei) and _c5 (body) channel TIFs by name."""
    c1_files = {f.stem.replace("_c1", ""): f for f in tif_paths
                if "_c1" in f.stem}
    c5_files = {f.stem.replace("_c5", ""): f for f in tif_paths
                if "_c5" in f.stem}
    common = sorted(set(c1_files) & set(c5_files))

    if common:
        return [
            {"name": key, "nuclei": c1_files[key], "body": c5_files[key]}
            for key in common
        ]

    return [
        {"name": f.stem, "nuclei": f, "body": None}
        for f in tif_paths
        if "(c1+c5)" not in f.stem
    ]


def load_image(pair: dict) -> np.ndarray:
    """Load image from NFS pair dict. Returns (Y, X, 2) or (Y, X) array."""
    nuc = imread(str(pair["nuclei"]))
    if nuc.ndim == 3:
        nuc = nuc.max(axis=-1) if nuc.shape[2] <= 4 else nuc[0]

    if pair["body"] is not None:
        body = imread(str(pair["body"]))
        if body.ndim == 3:
            body = body.max(axis=-1) if body.shape[2] <= 4 else body[0]
        return np.stack([body, nuc], axis=-1)

    return nuc


def main():
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    tif_paths = resolve_nfs_paths(
        args.dataset_id, args.omero_base,
        args.host, args.port,
    )
    if not tif_paths:
        print("No files resolved from OMERO. Check dataset ID.")
        return

    pairs = pair_images(tif_paths)
    if not pairs:
        print("No image pairs found after channel matching.")
        return

    print(f"Found {len(pairs)} image pairs")
    has_body = pairs[0]["body"] is not None
    channels = [1, 2] if has_body else [0, 0]
    print(f"Channel mode: {'dual (body+nuclei)' if has_body else 'single (nuclei only)'}")

    use_gpu = not args.no_gpu
    print(f"Loading Cellpose model '{args.model}' (GPU={use_gpu}) ...")
    model = models.CellposeModel(model_type=args.model, gpu=use_gpu)

    results_summary = []
    total_t0 = time.time()

    for i, pair in enumerate(pairs, 1):
        name = pair["name"]
        print(f"[{i}/{len(pairs)}] {name} ... ", end="", flush=True)

        img = load_image(pair)

        t0 = time.time()
        masks, _flows, _styles = model.eval(
            img,
            diameter=None,
            channels=channels,
            flow_threshold=0.4,
            cellprob_threshold=0.0,
        )
        seg_time = time.time() - t0

        n_cells = int(masks.max())
        intensity_img = img if img.ndim == 2 else img[..., 1]
        measurements = extract_measurements(masks, intensity_img)
        save_results(masks, measurements, args.output_dir, name)

        print(f"{n_cells} cells in {seg_time:.1f}s")
        results_summary.append({
            "name": name,
            "n_cells": n_cells,
            "seg_time_s": round(seg_time, 2),
            "source": "omero_nfs",
        })

    total_time = time.time() - total_t0

    summary_df = pd.DataFrame(results_summary)
    summary_path = args.output_dir / "summary.csv"
    summary_df.to_csv(summary_path, index=False)

    print(f"\n{'='*60}")
    print(f"Done. {summary_df['n_cells'].sum()} total cells "
          f"across {len(pairs)} images in {total_time:.1f}s")
    print(f"Results: {args.output_dir}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
