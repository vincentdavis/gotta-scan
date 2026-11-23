"""GOTTA.SCAN desktop sidecar — mesh geometry over a JSON CLI.

The Tauri app shells out to this and reads JSON from stdout. Commands return a
single JSON object; errors return {"error": "..."} with a non-zero exit code.

  uv run python cli.py measure-foot <mesh.obj>
"""

import argparse
import json
import sys

from gottascan_geometry.foot import (
    FootMeasurementError,
    load_foot_mesh,
    measure_foot,
)


def _emit(obj: dict, *, error: bool = False) -> None:
    print(json.dumps(obj))
    sys.exit(1 if error else 0)


def cmd_measure_foot(args) -> None:
    try:
        mesh = load_foot_mesh(args.mesh)
        _emit(measure_foot(mesh).as_dict())
    except FootMeasurementError as exc:
        _emit({'error': str(exc)}, error=True)
    except Exception as exc:  # malformed mesh / unreadable file
        _emit({'error': f'{type(exc).__name__}: {exc}'}, error=True)


def main() -> None:
    parser = argparse.ArgumentParser(prog='gottascan-sidecar')
    sub = parser.add_subparsers(dest='command', required=True)

    mf = sub.add_parser('measure-foot', help='Footwear measurements from an OBJ mesh (metres in, mm out).')
    mf.add_argument('mesh', help='Path to the reconstructed .obj')
    mf.set_defaults(func=cmd_measure_foot)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
