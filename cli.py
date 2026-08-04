#!/usr/bin/env python3
"""
cli.py -- command line front-end for Seedance Frame Doctor.

Usage:
  Report only (recommended first step):
    python cli.py report --input clip.mp4

  Apply the fix:
    python cli.py fix --input clip.mp4 --output clip_fixed.mov

  Apply the fix with custom thresholds / target fps / codec:
    python cli.py fix --input clip.mp4 --output clip_fixed.mov \
        --fps 24 --hi 768 --lo 320 --frac 0.33 --codec prores_ks
"""

import argparse
import sys

import core


def cmd_report(args):
    print(f"Analyzing {args.input} ...")
    try:
        results, fps = core.analyze_duplicates(
            args.input, diff_threshold=args.threshold, downscale_width=args.downscale,
        )
    except core.ToolError as e:
        sys.exit(f"Error: {e}")

    summary = core.summarize(results, fps)
    print()
    print(core.format_report(summary))
    print(
        "\nIf the pattern above looks periodic (e.g. every 3rd/4th frame) and "
        "consistent, the 'fix' mode below is a safe next step.\n"
        "If duplicates are scattered/random or near zero, this clip may "
        "not have the issue, or the threshold needs adjusting "
        f"(--threshold, currently {args.threshold:.2f})."
    )


def cmd_fix(args):
    print(f"Source: {args.input}")
    try:
        info, target_fps, summary = core.run_fix(
            args.input, args.output,
            threshold=args.threshold, downscale_width=args.downscale,
            fps=args.fps, interp_mode=args.interp_mode, codec=args.codec,
            crf=args.crf, model=args.model, gpu_id=args.gpu,
            on_line=print,
        )
    except core.ToolError as e:
        sys.exit(f"Error: {e}")

    print(f"\nDone. Removed {summary['dup_count']} of {summary['total']} frames, "
          f"retimed to {target_fps:.3f} fps.")
    print(f"Output written to: {args.output}")
    print(
        "Recommended: run 'report' mode on the output to confirm the "
        "duplicate pattern is gone before dropping this into the comp."
    )


def main():
    parser = argparse.ArgumentParser(
        description="Detect and fix Seedance 2.0 held/duplicate-frame artifacts."
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    p_report = sub.add_parser("report", help="Analyze a clip, no file written.")
    p_report.add_argument("--input", required=True)
    p_report.add_argument("--threshold", type=float, default=2.0)
    p_report.add_argument("--downscale", type=int, default=320)
    p_report.set_defaults(func=cmd_report)

    p_fix = sub.add_parser("fix", help="Remove duplicates and retime.")
    p_fix.add_argument("--input", required=True)
    p_fix.add_argument("--output", required=True)
    p_fix.add_argument("--threshold", type=float, default=2.0,
                        help="Same meaning as in 'report' -- use the same value "
                             "you confirmed worked there.")
    p_fix.add_argument("--downscale", type=int, default=320)
    p_fix.add_argument("--fps", type=float, default=None)
    p_fix.add_argument("--interp-mode", dest="interp_mode", default="blend",
                        choices=["blend", "mci", "dup", "rife"],
                        help="blend (default): safe, cross-blends real frames, "
                             "cannot produce structural artifacts, slight ghost "
                             "during fast motion. mci: sharper motion-compensated "
                             "interpolation, but can tear/warp at occlusion edges "
                             "and fine detail (hair, fur, busy backgrounds). "
                             "rife: AI optical-flow interpolation (requires "
                             "rife-ncnn-vulkan, see README) -- handles occlusion "
                             "and fine detail far better than blend/mci.")
    p_fix.add_argument("--model", default=None,
                        help="rife: model folder name (e.g. rife-v4.6). "
                             "Defaults to the best one found next to the binary.")
    p_fix.add_argument("--gpu", type=int, default=None,
                        help="rife: GPU id to use (-1 forces CPU). "
                             "Default: auto-detect.")
    p_fix.add_argument("--codec", default="prores_ks",
                        choices=["prores_ks", "libx264", "libx265"])
    p_fix.add_argument("--crf", type=int, default=16)
    p_fix.set_defaults(func=cmd_fix)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
