"""
omero_import.py
---------------
Upload a folder (or single image) into an OMERO server as a new Dataset
using the `omero` CLI under the hood.

Prerequisites
-------------
1. `omero-py` installed in your Python environment
   (e.g. `pip install omero-py` or `conda install -c ome omero-py`).
2. The OMERO.server client bundle downloaded and unzipped locally,
   and the OMERODIR environment variable pointing to it, e.g.:
       export OMERODIR=/path/to/OMERO.server-x.x.x-ice36-bxx
3. Network access to the target OMERO.server.

Typical usage
-------------
    # Import for yourself
    export OMERODIR=$(pwd)/OMERO.server-5.6.17-ice36
    python omero_import.py --server "omero.scilifelab.se"  --user "<username>" --password "<password>" --project-id "<project_id>" --dataset "<dataset-name>" --path "<path-to-images>"
 
"""

import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd, capture=False, check=True, env=None):
    """Run a shell command and stream/capture its output."""
    print(f"\n$ {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        env=env,
        check=False,
    )
    if capture:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
    if check and result.returncode != 0:
        raise SystemExit(
            f"Command failed with exit code {result.returncode}: {' '.join(cmd)}"
        )
    return result


def check_prerequisites():
    """Make sure `omero` CLI is callable and OMERODIR is set."""
    if not shutil.which("omero"):
        raise SystemExit(
            "ERROR: `omero` CLI not found on PATH. "
            "Activate the environment that has omero-py installed."
        )
    if not os.environ.get("OMERODIR"):
        raise SystemExit(
            "ERROR: OMERODIR is not set. "
            "Export it to the path of your unzipped OMERO.server bundle."
        )


def login(server, user, password, port, sudo_target=None):
    """
    Log in to OMERO via the CLI. Optionally sudo as another user.
    Uses --password so the script is non-interactive; consider using
    a session key or omero sessions for production use.
    """
    cmd = ["omero"]
    if sudo_target:
        cmd += ["--sudo", user, "-u", sudo_target]
    else:
        cmd += ["-u", user]
    cmd += ["-s", server, "-p", str(port), "-w", password, "login"]
    run(cmd)


def logout():
    run(["omero", "logout"], check=False)


def create_dataset(name, project_id=None):
    """
    Create a new Dataset and (optionally) link it under a Project.
    Returns the numeric Dataset ID.
    """
    result = run(
        ["omero", "obj", "new", "Dataset", f"name={name}"],
        capture=True,
    )
    # Output looks like: "Dataset:123"
    match = re.search(r"Dataset:(\d+)", result.stdout)
    if not match:
        raise SystemExit(f"Could not parse Dataset ID from output:\n{result.stdout}")
    dataset_id = match.group(1)
    print(f"Created Dataset:{dataset_id} ('{name}')")

    if project_id:
        run([
            "omero", "obj", "new", "ProjectDatasetLink",
            f"parent=Project:{project_id}",
            f"child=Dataset:{dataset_id}",
        ])

    return dataset_id


def import_data(dataset_id, path, in_place=False, extra_args=None):
    """Run `omero import` into the given Dataset."""
    target = Path(path)
    if not target.exists():
        raise SystemExit(f"ERROR: import path does not exist: {target}")

    cmd = ["omero", "import", "-d", str(dataset_id), "--encrypted", "true"]
    if in_place:
        cmd.append("--transfer=ln_s")
    if extra_args:
        cmd += extra_args
    cmd.append(str(target))
    run(cmd)


def parse_args():
    p = argparse.ArgumentParser(
        description="Upload a folder/image to OMERO as a new Dataset using the CLI."
    )
    p.add_argument("--server", required=True, help="OMERO server hostname")
    p.add_argument("--port", default=4064, type=int, help="OMERO server port (default 4064)")
    p.add_argument("--user", default=None, help="Login username (prompted if omitted)")
    p.add_argument(
        "--password",
        default=os.environ.get("OMERO_PASSWORD"),
        help="Login password (or set OMERO_PASSWORD env var)",
    )
    p.add_argument(
        "--sudo-target",
        help="Username to import on behalf of (requires admin privileges)",
    )
    p.add_argument("--dataset", required=True, help="Name of the Dataset to create")
    p.add_argument(
        "--project-id",
        help="Optional existing Project ID to link the new Dataset under",
    )
    p.add_argument(
        "--path",
        required=True,
        help="Path to a single image or a folder of images to import",
    )
    p.add_argument(
        "--in-place",
        action="store_true",
        help="Use --transfer=ln_s (must run on the OMERO server host)",
    )
    p.add_argument(
        "--extra",
        nargs=argparse.REMAINDER,
        help="Any extra args passed verbatim to `omero import` "
             "(put them at the end of the command line)",
    )
    return p.parse_args()


def main():
    args = parse_args()
    if not args.user:
        args.user = input("OMERO username: ")
    if not args.password:
        args.password = getpass.getpass("OMERO password: ")
    check_prerequisites()

    try:
        login(
            server=args.server,
            user=args.user,
            password=args.password,
            port=args.port,
            sudo_target=args.sudo_target,
        )
        dataset_id = create_dataset(args.dataset, project_id=args.project_id)
        import_data(
            dataset_id=dataset_id,
            path=args.path,
            in_place=args.in_place,
            extra_args=args.extra,
        )
        print(f"\n import finished. Dataset:{dataset_id}")
    finally:
        logout()


if __name__ == "__main__":
    main()

