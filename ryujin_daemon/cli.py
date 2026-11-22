"""Command-line entry point for the Ryujin III frame pusher daemon."""

from __future__ import annotations

import argparse
import sys

from .daemon import FramePusherDaemon
from .layout import export_schema, load_layout
from .mock import MockRyujinBackend
from .usb import RyujinUsbBackend


def build_backend(args):
    if args.backend == "mock":
        return MockRyujinBackend(available=not args.unavailable)
    return RyujinUsbBackend()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ryujin-frame-pusher")
    parser.add_argument("--backend", choices=("usb", "mock"), default="usb")
    parser.add_argument("--fps", type=float, default=1.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--unavailable", action="store_true", help="mock: simulate absent device")
    parser.add_argument("--once", action="store_true", help="push one frame and exit")
    parser.add_argument("--frames", type=int, default=None, help="push N accepted frames then exit")
    parser.add_argument("--layout", help="path to a layout JSON to validate/apply")
    parser.add_argument("--export-schema", help="write the layout JSON schema to this path and exit")
    args = parser.parse_args(argv)

    if args.export_schema:
        export_schema(args.export_schema)
        print(f"wrote layout schema to {args.export_schema}")
        return 0

    width, height = args.width, args.height
    if args.layout:
        layout = load_layout(args.layout)
        width, height = layout["width"], layout["height"]

    backend = build_backend(args)
    daemon = FramePusherDaemon(backend, width=width, height=height, fps=args.fps)

    def log(message):
        print(message, flush=True)

    if args.once:
        ok = daemon.push_test_frame()
        log("push_test_frame: " + ("ok" if ok else "device unavailable"))
        return 0 if ok else 1
    daemon.run(total_frames=args.frames, log=log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
