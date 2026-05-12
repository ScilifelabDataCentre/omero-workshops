#!/usr/bin/env python
"""
push_results_to_omero.py — Push masks, measurements, and ROIs back to OMERO.

Attaches Cellpose segmentation results to their corresponding OMERO images:
  - Mask TIFFs and measurement CSVs as file annotations
  - Cell contours as OMERO polygon ROIs (viewable in OMERO.web)

Usage:
    python push_results_to_omero.py --dataset-id 42 [--results-dir <path>] [--skip-rois] 
"""

import argparse
from pathlib import Path

import numpy as np
from skimage.measure import find_contours
from tifffile import imread

from utils import omero_connect

NAMESPACE = "scilifelab.se/omero/analysis/cellpose"


def parse_args():
    p = argparse.ArgumentParser(
        description="Upload Cellpose results back to OMERO")
    p.add_argument("--dataset-id", type=int, required=True,
                   help="OMERO Dataset ID to attach results to")
    p.add_argument("--results-dir", type=Path,
                   default=Path("results") / "local",
                   help="Directory containing *_masks.tiff and *_measurements.csv")
    p.add_argument("--skip-rois", action="store_true",
                   help="Skip ROI contour upload (faster)")
    p.add_argument("--host", default="omero.scilifelab.se")
    p.add_argument("--port", type=int, default=4064)
    p.add_argument("--namespace", default=NAMESPACE, help="OMERO namespace")
    return p.parse_args()


def attach_files_to_images(conn, matched_pairs, results_dir: Path):
    """Attach per-image mask TIFFs and measurement CSVs to their OMERO Images."""
    for img, mask_path in matched_pairs:
        img_name = img.getName()
        csv_path = results_dir / mask_path.name.replace("_masks.tiff",
                                                         "_measurements.csv")
        for fpath in [mask_path, csv_path]:
            if not fpath.exists():
                continue
            mimetype = "image/tiff" if fpath.suffix == ".tiff" else "text/csv"
            print(f"  {img_name} <- {fpath.name} ... ", end="", flush=True)
            ann = conn.createFileAnnfromLocalFile(
                str(fpath), mimetype=mimetype, ns=NAMESPACE)
            img.linkAnnotation(ann)
            print("done")


def attach_summary_to_dataset(conn, dataset, results_dir: Path):
    """Attach only the summary CSV to the Dataset."""
    summary = results_dir / "summary.csv"
    if summary.exists():
        print(f"  Attaching summary.csv to dataset ... ", end="", flush=True)
        ann = conn.createFileAnnfromLocalFile(
            str(summary), mimetype="text/csv", ns=NAMESPACE)
        dataset.linkAnnotation(ann)
        print("done")


def upload_rois_for_image(conn, image_id: int, mask_path: Path,
                          max_cells: int = 500):
    """Convert a label mask to OMERO polygon ROIs and upload them."""
    import omero
    from omero.model import PolygonI, RoiI
    from omero.rtypes import rdouble, rint, rstring

    masks = imread(str(mask_path))
    n_cells = int(masks.max())

    if n_cells > max_cells:
        print(f"    (capping ROIs at {max_cells}/{n_cells} cells)")
        n_cells = max_cells

    update_svc = conn.getUpdateService()
    count = 0

    for cell_id in range(1, n_cells + 1):
        binary = (masks == cell_id).astype(np.uint8)
        contours = find_contours(binary, 0.5)
        if not contours:
            continue

        contour = max(contours, key=len)
        if len(contour) < 4:
            continue

        # Subsample long contours to keep transfer fast
        if len(contour) > 200:
            step = len(contour) // 200
            contour = contour[::step]

        points_str = " ".join(f"{c[1]:.1f},{c[0]:.1f}" for c in contour)

        roi = RoiI()
        roi.setImage(omero.model.ImageI(image_id, False))

        polygon = PolygonI()
        polygon.setPoints(rstring(points_str))
        polygon.setTextValue(rstring(f"Cell {cell_id}"))
        polygon.setTheZ(rint(0))
        polygon.setTheT(rint(0))
        roi.addShape(polygon)

        update_svc.saveAndReturnObject(roi)
        count += 1

    return count


def match_results_to_images(conn, dataset, results_dir: Path):
    """Try to match mask files to OMERO images by name similarity."""
    images = list(dataset.listChildren())
    mask_files = {f.stem.replace("_masks", ""): f
                  for f in results_dir.glob("*_masks.tiff")}

    matched = []
    for img in images:
        img_stem = Path(img.getName()).stem
        # Try exact match or prefix match
        for mask_key, mask_path in mask_files.items():
            if mask_key in img_stem or img_stem in mask_key:
                matched.append((img, mask_path))
                break

    return matched


def main():
    args = parse_args()

    if not args.results_dir.exists():
        print(f"ERROR: Results directory not found: {args.results_dir}")
        return
    
    if args.namespace:
        NAMESPACE = args.namespace

    conn = omero_connect(host=args.host, port=args.port)
    try:
        dataset = conn.getObject("Dataset", args.dataset_id)
        if dataset is None:
            print(f"ERROR: Dataset {args.dataset_id} not found in OMERO")
            return

        print(f"Dataset: '{dataset.getName()}' (id={args.dataset_id})")

        matched = match_results_to_images(conn, dataset, args.results_dir)
        if not matched:
            print("WARNING: No image-to-mask matches found. "
                  "Cannot attach results.")
        else:
            print(f"Matched {len(matched)} images to results")

            print("\n--- Attaching result files to images ---")
            attach_files_to_images(conn, matched, args.results_dir)

            if not args.skip_rois:
                print("\n--- Uploading ROI contours ---")
                for img, mask_path in matched:
                    print(f"  {img.getName()} <- {mask_path.name} ... ",
                          end="", flush=True)
                    n = upload_rois_for_image(conn, img.getId(), mask_path)
                    print(f"{n} ROIs")

        print("\n--- Attaching summary to dataset ---")
        attach_summary_to_dataset(conn, dataset, args.results_dir)

        print("\nDone. View results in OMERO.web at "
              f"https://omero.scilifelab.se/webclient/?show=dataset-{args.dataset_id}")

    finally:
        conn.close()
        print("OMERO connection closed.")


if __name__ == "__main__":
    main()
