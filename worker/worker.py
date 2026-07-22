#!/usr/bin/env python3
"""GOTTA.SCAN Mac worker.

Polls the web app for queued scans, runs Apple's PhotogrammetrySession via the
bundled Swift CLI, and posts the reconstructed USDZ back.

Usage:
  uv run --with requests worker/worker.py --server http://127.0.0.1:8000 [--once] [--simulate]

The Swift CLI is compiled on first use (cached in worker/.build/).
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import requests

WORKER_DIR = Path(__file__).resolve().parent
CLI_SOURCE = WORKER_DIR / 'PhotogrammetryCLI.swift'
CLI_BINARY = WORKER_DIR / '.build' / 'photogrammetry-cli'
IMAGE_EXTENSIONS = {'.heic', '.heif', '.jpg', '.jpeg', '.png'}


def log(message: str) -> None:
    print(f'[worker] {message}', flush=True)


def ensure_cli() -> Path:
    """Compile the Swift CLI if missing or older than its source."""
    if CLI_BINARY.exists() and CLI_BINARY.stat().st_mtime >= CLI_SOURCE.stat().st_mtime:
        return CLI_BINARY
    CLI_BINARY.parent.mkdir(exist_ok=True)
    log('compiling PhotogrammetryCLI…')
    subprocess.run(
        ['swiftc', '-O', '-parse-as-library', str(CLI_SOURCE), '-o', str(CLI_BINARY)],
        check=True,
    )
    return CLI_BINARY


def _find_bundle_root(extracted: Path) -> Path:
    """Mirror of the server's logic (webapp/scans/services.py) — the two ends
    must agree on what counts as processable or queued scans fail forever."""
    if (extracted / 'metadata.json').exists():
        return extracted
    for child in sorted(extracted.iterdir()):
        if child.is_dir() and not child.name.startswith('__MACOSX') and (child / 'metadata.json').exists():
            return child
    for child in sorted(extracted.iterdir()):
        if child.is_dir() and not child.name.startswith('__MACOSX'):
            return child
    return extracted


def _has_images(directory: Path) -> bool:
    return directory.is_dir() and any(
        p.suffix.lower() in IMAGE_EXTENSIONS for p in directory.iterdir() if p.is_file()
    )


def find_images_dir(extracted: Path) -> Path | None:
    root = _find_bundle_root(extracted)
    if (root / 'Images').is_dir():
        return root / 'Images'
    candidates = [root] + [p for p in root.iterdir() if p.is_dir()] if root.is_dir() else []
    for candidate in candidates:
        if _has_images(candidate):
            return candidate
    return None


def post_with_retries(session: requests.Session, url: str, attempts: int = 3,
                      delay: float = 5.0, **kwargs) -> requests.Response:
    """Retry transient failures — a brief server restart must not cost an
    hour-long reconstruction or strand a scan."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.post(url, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            log(f'POST {url} failed (attempt {attempt + 1}/{attempts}): {exc}')
            if attempt < attempts - 1:
                time.sleep(delay)
    raise last_error


def process_job(session: requests.Session, server: str, job: dict, args) -> None:
    scan_id = job['scan_id']
    log(f"processing {scan_id} ({job.get('image_count', '?')} images)")

    with tempfile.TemporaryDirectory(prefix=f'gottascan-{scan_id}-') as tmp:
        tmp_path = Path(tmp)
        bundle_path = tmp_path / 'bundle.zip'

        with session.get(f"{server}{job['bundle_url']}", stream=True, timeout=600) as response:
            response.raise_for_status()
            with open(bundle_path, 'wb') as out:
                shutil.copyfileobj(response.raw, out)

        extracted = tmp_path / 'extracted'
        with zipfile.ZipFile(bundle_path) as zf:
            for member in zf.namelist():
                if member.startswith('/') or '..' in Path(member).parts:
                    raise RuntimeError(f'unsafe bundle member: {member}')
            zf.extractall(extracted)

        images_dir = find_images_dir(extracted)
        if images_dir is None:
            raise RuntimeError('no images found in bundle')

        output = tmp_path / 'model.usdz'
        if args.simulate:
            log('simulate mode: writing placeholder model')
            output.write_bytes(b'GOTTASCAN-SIMULATED-USDZ')
            cli_log = 'simulated'
        else:
            cli = ensure_cli()
            log(f'reconstructing at detail={args.detail} (this can take a while)…')
            result = subprocess.run(
                [str(cli), str(images_dir), str(output), args.detail],
                capture_output=True, text=True, timeout=args.timeout,
            )
            cli_log = (result.stdout + result.stderr)[-20000:]
            if result.returncode != 0 or not output.exists():
                raise RuntimeError(f'reconstruction failed (exit {result.returncode}):\n{cli_log[-2000:]}')

        # Retry with the model still on disk: throwing away a finished
        # reconstruction over a transient network blip is the worst outcome.
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with open(output, 'rb') as model_file:
                    response = session.post(
                        f'{server}/api/worker/{scan_id}/complete',
                        files={'usdz': ('model.usdz', model_file, 'model/vnd.usdz+zip')},
                        data={'log': cli_log},
                        timeout=600,
                    )
                response.raise_for_status()
                last_error = None
                break
            except requests.RequestException as exc:
                last_error = exc
                log(f'complete POST failed (attempt {attempt + 1}/3): {exc}')
                time.sleep(5)
        if last_error is not None:
            raise last_error
        log(f'{scan_id} complete')


def main() -> int:
    parser = argparse.ArgumentParser(description='GOTTA.SCAN Mac photogrammetry worker')
    parser.add_argument('--server', default='http://127.0.0.1:8000')
    parser.add_argument('--token', default='dev-worker-token')
    parser.add_argument('--detail', default='full',
                        choices=['preview', 'reduced', 'medium', 'full', 'raw'])
    parser.add_argument('--interval', type=float, default=10.0, help='poll interval seconds')
    parser.add_argument('--timeout', type=float, default=3600.0, help='per-scan reconstruction timeout')
    parser.add_argument('--once', action='store_true', help='process at most one job, then exit')
    parser.add_argument('--simulate', action='store_true', help='skip reconstruction, post placeholder model')
    args = parser.parse_args()

    server = args.server.rstrip('/')
    session = requests.Session()
    session.headers['X-Worker-Token'] = args.token

    log(f'polling {server} every {args.interval}s (detail={args.detail})')
    while True:
        try:
            response = session.post(f'{server}/api/worker/claim', timeout=30)
            response.raise_for_status()
            job = response.json().get('job')
        except requests.RequestException as exc:
            log(f'server unreachable: {exc}')
            job = None

        if job:
            try:
                process_job(session, server, job, args)
            except Exception as exc:
                log(f"job {job['scan_id']} failed: {exc}")
                try:
                    post_with_retries(
                        session,
                        f"{server}/api/worker/{job['scan_id']}/fail",
                        data={'error': str(exc)[:2000]},
                        timeout=60,
                    )
                except requests.RequestException:
                    log('could not report failure; server reaper will requeue the scan')
            except BaseException:
                # Ctrl-C / kill mid-job: put the scan back in the queue instead
                # of stranding it in 'processing' until the reaper window.
                log(f"interrupted during {job['scan_id']}; requeueing")
                try:
                    session.post(f"{server}/api/worker/{job['scan_id']}/requeue", timeout=30)
                except requests.RequestException:
                    log('could not requeue; server reaper will recover the scan')
                raise
            if args.once:
                return 0
        else:
            if args.once:
                return 0
            time.sleep(args.interval)


if __name__ == '__main__':
    sys.exit(main())
