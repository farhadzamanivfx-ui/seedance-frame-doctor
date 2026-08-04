"""
core.py

Shared engine for Seedance Frame Doctor: detects and fixes the
Seedance 2.0 "held/duplicate frame" artifact (repeated frames baked
into the output, causing stutter/jump on scrub).

No UI code lives here -- this module is imported by both the CLI
(cli.py) and the desktop GUI (gui_app.py), so the detection/fix logic
only exists in one place.

Requires: ffmpeg, ffprobe, opencv-python (cv2), numpy.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np


class ToolError(Exception):
    """Raised for any user-facing failure (missing ffmpeg, bad file, etc.)."""


def _candidate_dirs():
    """
    Directories to check, in order, for a bundled ffmpeg/ffprobe copy.

    Important PyInstaller detail: with --onefile, files added via
    --add-binary are extracted at runtime into a TEMPORARY folder
    (sys._MEIPASS), which is NOT the same folder as the .exe itself
    (sys.executable's parent). sys._MEIPASS must be checked first for
    onefile builds to actually find the bundled ffmpeg/ffprobe. For
    --onedir builds, sys._MEIPASS equals the exe's own folder, so
    checking it covers both cases.

    We also check the exe's own folder as a second option, so a user
    can manually drop/replace ffmpeg.exe next to the built .exe
    without rebuilding.
    """
    dirs = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass))
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)
    else:
        dirs.append(Path(__file__).resolve().parent)
    return dirs


def find_ffmpeg_tool(name):
    """
    Locate ffmpeg/ffprobe. Checks, in order:
      1. PyInstaller's extracted bundle dir (sys._MEIPASS), if frozen
      2. The folder containing the running .exe / script
      3. System PATH
    Returns the full path/command to use, or raises ToolError.
    """
    exe_name = f"{name}.exe" if os.name == "nt" else name
    for d in _candidate_dirs():
        candidate = d / exe_name
        if candidate.exists():
            return str(candidate)

    # Fall back to PATH
    from shutil import which
    found = which(name)
    if found:
        return found

    raise ToolError(
        f"Could not find '{name}'. Place '{exe_name}' next to this "
        f"application, or install ffmpeg and ensure it's on your PATH."
    )


def rife_available():
    """
    Check whether the rife_ncnn_vulkan_python package (the AI
    interpolation engine) and at least one bundled model are usable.

    This is a Python package, not a subprocess binary -- it ships a
    compiled extension module (.pyd/.so) built on top of ncnn+Vulkan
    (https://github.com/TNTwise/rife-ncnn-vulkan-python, MIT-licensed
    upstream RIFE model). It's a separate, optional component: only
    needed for interp_mode="rife", blend/mci work without it.
    """
    try:
        import rife_ncnn_vulkan_python  # noqa: F401
    except ImportError:
        return False, None
    models_dir = Path(rife_ncnn_vulkan_python.__file__).resolve().parent / "models"
    if not models_dir.exists():
        return False, None
    return True, models_dir


def run(cmd, capture=False, on_line=None):
    """
    Run a subprocess. If on_line is given, it's called with each line of
    stderr as it streams (used by the GUI to show live ffmpeg progress).
    Raises ToolError with the captured output on non-zero exit.
    """
    if on_line is not None:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        lines = []
        for line in proc.stdout:
            lines.append(line)
            on_line(line.rstrip())
        proc.wait()
        if proc.returncode != 0:
            raise ToolError("".join(lines))
        return "".join(lines)

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise ToolError(result.stderr)
    return result.stdout if capture else None


def probe_video(path):
    """Get fps, frame count, width, height via ffprobe."""
    ffprobe = find_ffmpeg_tool("ffprobe")
    cmd = [
        ffprobe, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate,nb_frames,width,height,avg_frame_rate",
        "-of", "json",
        str(path),
    ]
    out = run(cmd, capture=True)
    streams = json.loads(out).get("streams")
    if not streams:
        raise ToolError(f"No video stream found in: {path}")
    data = streams[0]

    def parse_rate(rate_str):
        num, den = rate_str.split("/")
        return float(num) / float(den)

    fps = parse_rate(data.get("avg_frame_rate") or data["r_frame_rate"])
    nb_frames = data.get("nb_frames")
    width = int(data["width"])
    height = int(data["height"])

    if not nb_frames or nb_frames == "N/A":
        cmd_count = [
            ffprobe, "-v", "error",
            "-select_streams", "v:0",
            "-count_frames",
            "-show_entries", "stream=nb_read_frames",
            "-of", "json",
            str(path),
        ]
        out_count = run(cmd_count, capture=True)
        nb_frames = json.loads(out_count)["streams"][0]["nb_read_frames"]

    return {
        "fps": fps,
        "nb_frames": int(nb_frames),
        "width": width,
        "height": height,
    }


def analyze_duplicates(path, diff_threshold=2.0, downscale_width=320, progress_cb=None):
    """
    Walk the video frame by frame, measuring mean absolute pixel
    difference against the previous frame (downscaled grayscale, for
    speed). Frames below diff_threshold are flagged as near-duplicates.

    progress_cb, if given, is called with (current_index, total_estimate)
    so a UI can show a progress bar.

    Returns (results, fps) where results is a list of dicts:
      {index, timestamp_s, diff, is_duplicate}
    """
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ToolError(f"Could not open video: {path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    total_estimate = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    results = []
    prev_gray = None
    idx = 0

    # First pass: collect raw diffs only (no duplicate decision yet).
    # The duplicate decision needs to see the NEXT frame's diff too,
    # so we do it in a second pass below.
    while True:
        ok, frame = cap.read()
        if not ok:
            break

        h, w = frame.shape[:2]
        scale = downscale_width / w
        small = cv2.resize(frame, (downscale_width, max(1, int(h * scale))))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)

        if prev_gray is not None:
            diff = float(np.mean(np.abs(gray - prev_gray)))
        else:
            diff = None

        results.append({
            "index": idx,
            "timestamp_s": round(idx / fps, 3),
            "diff": diff,
            "is_duplicate": False,  # set in the second pass
        })

        prev_gray = gray
        idx += 1
        if progress_cb:
            progress_cb(idx, total_estimate)

    cap.release()

    # Second pass: snap-aware duplicate detection.
    #
    # A frame is flagged as a Seedance held duplicate only if BOTH:
    #   (a) its diff to the previous frame is below diff_threshold
    #       (it's pixel-close to the previous frame), AND
    #   (b) the NEXT frame's diff is significantly larger
    #       (a "snap" back to real motion follows it)
    #
    # The snap test (b) is what distinguishes a Seedance held frame
    # ("real, real, HELD, snap-back-to-real") from natural slow motion
    # at the end of a shot ("real, slow, slow, slow", no snap). With
    # only rule (a), the tail of a shot where the camera locks off /
    # action settles was being mistakenly flagged as duplicates and
    # then dropped at the trailing-drop stage, shortening the clip.
    snap_factor = 2.0  # next-frame diff must be at least this many times
                       # the held-frame diff to count as a snap
    for i in range(len(results)):
        r = results[i]
        if r["diff"] is None or r["diff"] >= diff_threshold:
            continue
        # find the next frame's diff (skip None which only happens for index 0)
        next_diff = results[i + 1]["diff"] if i + 1 < len(results) else None
        if next_diff is None:
            # last frame of the clip -- no snap possible to confirm with;
            # be conservative and treat it as real, not a duplicate.
            continue
        if next_diff > snap_factor * max(r["diff"], 0.1):
            r["is_duplicate"] = True

    return results, fps


def summarize(results, fps):
    """Turn raw per-frame results into a summary dict (no printing)."""
    dup_frames = [r for r in results if r["is_duplicate"]]
    total = len(results)
    dup_count = len(dup_frames)
    pct = (dup_count / total * 100) if total else 0.0

    most_common_gap = None
    consistency = None
    indices = [r["index"] for r in dup_frames]
    gaps = [b - a for a, b in zip(indices[:-1], indices[1:])]
    if gaps:
        most_common_gap = max(set(gaps), key=gaps.count)
        consistency = gaps.count(most_common_gap) / len(gaps) * 100

    return {
        "total": total,
        "fps": fps,
        "dup_count": dup_count,
        "pct": pct,
        "most_common_gap": most_common_gap,
        "consistency": consistency,
        "flagged": dup_frames,
    }


def format_report(summary):
    """Render a summary dict as human-readable text (CLI + GUI share this)."""
    lines = []
    lines.append(f"Total frames analyzed: {summary['total']}")
    lines.append(f"Source fps:            {summary['fps']:.3f}")
    lines.append(
        f"Duplicate/held frames: {summary['dup_count']} ({summary['pct']:.1f}%)"
    )

    if not summary["flagged"]:
        lines.append("No duplicate-frame pattern detected at this threshold.")
        return "\n".join(lines)

    if summary["most_common_gap"] is not None:
        lines.append(
            f"Most common gap between duplicates: every "
            f"{summary['most_common_gap']} frame(s) "
            f"({summary['consistency']:.0f}% of duplicates follow this pattern)"
        )

    lines.append("\nFirst 20 flagged frames (index | timestamp | diff):")
    for r in summary["flagged"][:20]:
        lines.append(f"  {r['index']:>5} | {r['timestamp_s']:>7.3f}s | diff={r['diff']:.3f}")
    if summary["dup_count"] > 20:
        lines.append(f"  ... and {summary['dup_count'] - 20} more")

    return "\n".join(lines)


def build_select_expr(duplicate_indices):
    """
    Build an ffmpeg `select` filter expression that drops exactly the
    given 0-indexed frame numbers and keeps every other frame.

    This is deliberately driven by the *same* detection used for the
    report (analyze_duplicates), instead of ffmpeg's mpdecimate filter,
    which uses its own separate per-block heuristic with its own
    thresholds. Using two independent detectors meant the fix could
    drop a different (often smaller) set of frames than the ones the
    report actually found -- this guarantees fix removes exactly what
    report flagged.
    """
    if not duplicate_indices:
        return None  # nothing to drop
    # ffmpeg filter-chain syntax uses ',' to separate chained filters,
    # so a literal ',' inside eq(n,i) must be escaped as '\,' or it
    # would be misread as the end of this filter.
    terms = "+".join(f"eq(n\\,{i})" for i in duplicate_indices)
    return f"select='not({terms})'"


def build_fix_command(input_path, output_path, threshold=2.0, downscale_width=320,
                       fps=None, interp_mode="blend", codec="prores_ks", crf=16,
                       progress_cb=None):
    """
    Build the ffmpeg command for the fix step, using the exact same
    frame-diff detection as analyze_duplicates() (same threshold,
    same downscale_width) so what gets removed matches what 'report'
    found. Returns (cmd, info, target_fps, summary) -- summary is the
    same dict format produced by summarize(), so the GUI/CLI can show
    "removed N of N flagged frames" before running ffmpeg.

    Frame-count math (this matters -- it was wrong in an earlier
    version of this tool):

    Removing M duplicate frames out of N total leaves N-M frames. If
    those N-M frames are simply renumbered at the *original* fps
    (the naive approach), their new total duration becomes
    (N-M)/original_fps -- i.e. SHORTER than the source. Asking
    minterpolate to then output at the *same* original fps doesn't
    restore anything, because the timeline it's working from has
    already been compressed -- it just regenerates roughly the same
    (N-M) frames over the now-shorter duration.

    What we actually want is to keep the original duration (N /
    original_fps) and original frame count (N), but built from real
    in-between motion instead of held duplicates. So the N-M kept
    frames are first respaced across the *original* duration -- which
    means treating them as if they were shot at a slower
    "intermediate fps" of (N-M) * original_fps / N -- and *then*
    minterpolate brings that back up to the original fps, generating
    real motion-compensated in-between frames to fill the gap.
    """
    ffmpeg = find_ffmpeg_tool("ffmpeg")
    info = probe_video(input_path)
    target_fps = fps or info["fps"]

    results, source_fps = analyze_duplicates(
        input_path, diff_threshold=threshold, downscale_width=downscale_width,
        progress_cb=progress_cb,
    )
    summary = summarize(results, source_fps)
    duplicate_indices = [r["index"] for r in summary["flagged"]]

    select_expr = build_select_expr(duplicate_indices)

    # mi_mode=mci (motion-compensated interpolation) warps blocks of
    # pixels along estimated motion vectors to synthesize new frames.
    # At occlusion boundaries (e.g. a hair silhouette against sky)
    # combined with fine/thin structures (hair strands) and
    # independently-moving background elements (birds moving
    # differently than the head), the block-based motion estimation
    # breaks down and can produce visible geometric tearing -- often
    # wedge/triangle-shaped, from how overlapped block motion
    # compensation (mc_mode=aobmc) and variable block sizing
    # (vsbmc=1) divide the frame at conflicting-motion edges.
    #
    # mi_mode=blend instead cross-blends the two real surrounding
    # frames -- it can never produce structural/geometric tearing,
    # since it isn't warping anything, only mixing real pixel values.
    # The tradeoff is a soft double-exposure "ghost" during fast
    # motion instead of fully sharp in-betweens. For VFX delivery
    # where structural artifacts are unacceptable, blend is the safer
    # default; mci is sharper but should be spot-checked for tearing
    # around hair/fur, occlusion edges, and busy backgrounds.
    if interp_mode == "mci":
        interp_filter = (
            f"minterpolate=fps={target_fps}:mi_mode=mci:"
            f"mc_mode=aobmc:vsbmc=1"
        )
    else:
        interp_filter = f"minterpolate=fps={target_fps}:mi_mode={interp_mode}"

    total_frames = summary["total"]
    kept_frames = total_frames - summary["dup_count"]

    if select_expr and kept_frames > 0 and total_frames > 0:
        intermediate_fps = kept_frames * source_fps / total_frames
        vf = (
            f"{select_expr},setpts=N/({intermediate_fps:.6f}*TB),{interp_filter}"
        )
    else:
        # Nothing flagged as duplicate (or nothing left after removal)
        # -- just conform to target fps, no respacing needed.
        vf = interp_filter

    cmd = [ffmpeg, "-y", "-i", str(input_path), "-vf", vf, "-c:v", codec]

    if codec == "prores_ks":
        cmd += ["-profile:v", "3", "-pix_fmt", "yuv422p10le"]
    elif codec in ("libx264", "libx265"):
        cmd += ["-crf", str(crf), "-pix_fmt", "yuv420p"]

    cmd += ["-c:a", "copy", str(output_path)]
    return cmd, info, target_fps, summary


def run_fix(input_path, output_path, on_line=None, progress_cb=None, **kwargs):
    """
    Run the fix end to end. Dispatches to the AI (RIFE) path if
    interp_mode='rife', otherwise uses the classical ffmpeg path
    (blend/mci/dup via minterpolate).
    """
    if kwargs.get("interp_mode") == "rife":
        kwargs.pop("interp_mode", None)
        return run_fix_rife(input_path, output_path, on_line=on_line,
                             progress_cb=progress_cb, **kwargs)

    # model/gpu_id are RIFE-only options; the classical path doesn't
    # accept them, so drop them here if a caller passes them anyway
    # (e.g. the GUI always sends both regardless of selected mode).
    kwargs.pop("model", None)
    kwargs.pop("gpu_id", None)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd, info, target_fps, summary = build_fix_command(
        input_path, output_path, progress_cb=progress_cb, **kwargs
    )
    if on_line:
        on_line(
            f"Detected {summary['dup_count']} duplicate frame(s) out of "
            f"{summary['total']} -- removing exactly these, then "
            f"retiming to {target_fps:.3f} fps."
        )
    run(cmd, on_line=on_line)
    return info, target_fps, summary


def _pick_default_rife_model(models_dir):
    """Pick the best available bundled model, preferring newer versions."""
    if not models_dir:
        return None
    preference = ["rife-v4.6", "rife-v4", "rife-v3.1", "rife-v3.0",
                  "rife-v2.4", "rife-v2.3", "rife-v2", "rife-anime", "rife"]
    available = {p.name for p in Path(models_dir).iterdir() if p.is_dir()}
    for name in preference:
        if name in available:
            return name
    return next(iter(available), None)


def _gaps_from_duplicates(total_frames, duplicate_indices):
    """
    Walk the frame indices and group consecutive duplicate runs into
    gaps, each anchored by the kept frame immediately before it.
    Returns a list of (anchor_kept_index, gap_length) tuples, in
    order. A trailing run of duplicates with no kept frame after it
    (rare edge case: clip ends on a held frame) has no second anchor
    to interpolate toward and is reported separately so the caller
    can simply drop those frames instead of holding them.
    """
    gaps = []
    i = 0
    trailing_drop = 0
    while i < total_frames:
        if i not in duplicate_indices:
            i += 1
            continue
        # start of a run of duplicates
        run_start = i
        while i < total_frames and i in duplicate_indices:
            i += 1
        run_len = i - run_start
        anchor = run_start - 1  # last kept frame before this run
        if i < total_frames:
            gaps.append((anchor, run_len))
        else:
            trailing_drop = run_len
    return gaps, trailing_drop


def run_fix_rife(input_path, output_path, threshold=2.0, downscale_width=320,
                  fps=None, codec="prores_ks", crf=16, model=None, gpu_id=-1,
                  on_line=None, progress_cb=None):
    """
    AI-interpolation fix path using rife_ncnn_vulkan_python instead of
    ffmpeg's classical minterpolate. Unlike minterpolate's block-based
    motion compensation -- which can tear/warp at occlusion edges and
    fine detail (hair against a busy background, fur) -- RIFE uses a
    learned optical-flow network, which handles those cases far more
    robustly.

    Unlike the CLI-based approach (rife-ncnn-vulkan.exe with a global
    -n target-frame-count, which redistributes new frames evenly
    across the whole clip), this fills in exactly the right number of
    real interpolated frames at exactly each gap where duplicates were
    removed -- e.g. a 3-frame gap gets exactly 3 new frames at
    timesteps 1/4, 2/4, 3/4 between the two real frames bordering it.
    This preserves the original clip's frame count and timing
    structure precisely, frame-for-frame.

    gpu_id defaults to -1 (CPU) for safety on machines without a
    working Vulkan driver; pass e.g. 0 to use the primary GPU.
    """
    import numpy as np
    import cv2
    from PIL import Image
    from rife_ncnn_vulkan_python import Rife

    if gpu_id is None:
        gpu_id = -1

    ffmpeg = find_ffmpeg_tool("ffmpeg")
    available, models_dir = rife_available()
    if not available:
        raise ToolError(
            "rife_ncnn_vulkan_python is not installed or its models/ "
            "folder is missing. This is a separate, optional AI "
            "interpolation engine -- run setup_and_build.bat to install "
            "it, or 'pip install rife-ncnn-vulkan-python-tntwise' plus "
            "the bundled models folder."
        )

    info = probe_video(input_path)
    target_fps = fps or info["fps"]
    width, height = info["width"], info["height"]

    results, source_fps = analyze_duplicates(
        input_path, diff_threshold=threshold, downscale_width=downscale_width,
        progress_cb=progress_cb,
    )
    summary = summarize(results, source_fps)
    duplicate_indices = set(r["index"] for r in summary["flagged"])
    total_frames = summary["total"]

    if on_line:
        on_line(
            f"Detected {summary['dup_count']} duplicate frame(s) out of "
            f"{total_frames} -- will fill each gap with real AI-"
            f"interpolated frames (not a global redistribution)."
        )

    if not duplicate_indices:
        if on_line:
            on_line("No duplicates flagged -- nothing for RIFE to fill in; "
                     "conforming directly to target fps instead.")
        cmd = [ffmpeg, "-y", "-i", str(input_path),
               "-vf", f"minterpolate=fps={target_fps}:mi_mode=blend",
               "-c:v", codec]
        if codec == "prores_ks":
            cmd += ["-profile:v", "3", "-pix_fmt", "yuv422p10le"]
        elif codec in ("libx264", "libx265"):
            cmd += ["-crf", str(crf), "-pix_fmt", "yuv420p"]
        cmd += ["-c:a", "copy", str(output_path)]
        run(cmd, on_line=on_line)
        return info, target_fps, summary

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    chosen_model = model or _pick_default_rife_model(models_dir)
    if not chosen_model:
        raise ToolError(f"No RIFE model found in {models_dir}.")
    if on_line:
        on_line(f"Loading RIFE model '{chosen_model}' "
                f"({'GPU ' + str(gpu_id) if gpu_id >= 0 else 'CPU'})...")
    rife = Rife(gpuid=gpu_id, model=chosen_model, width=width, height=height)

    with tempfile.TemporaryDirectory(prefix="sfd_rife_") as tmp:
        tmp = Path(tmp)
        frames_out = tmp / "out"
        frames_out.mkdir()

        # Decode every frame once, in memory -- these clips are short
        # (a few hundred frames at most), so this is cheap and avoids
        # a slower extract-to-disk-then-reread round trip.
        cap = cv2.VideoCapture(str(input_path))
        all_frames = []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            all_frames.append(frame)
        cap.release()

        gaps, trailing_drop = _gaps_from_duplicates(total_frames, duplicate_indices)
        if trailing_drop and on_line:
            on_line(f"Note: {trailing_drop} trailing duplicate frame(s) at "
                     f"the very end have no following frame to interpolate "
                     f"toward, so they're dropped rather than held.")

        gap_lengths = {anchor: length for anchor, length in gaps}
        out_idx = 0

        def write_frame(bgr_array):
            nonlocal out_idx
            out_idx += 1
            cv2.imwrite(str(frames_out / f"{out_idx:08d}.png"), bgr_array)

        kept_count = total_frames - summary["dup_count"]
        processed = 0
        for i in range(total_frames):
            if i in duplicate_indices:
                continue
            write_frame(all_frames[i])
            processed += 1
            if on_line and processed % 10 == 0:
                on_line(f"Processed {processed}/{kept_count} unique frames...")

            if i in gap_lengths and i + 1 < total_frames:
                # find the next kept frame (the one right after this run)
                j = i + 1
                while j < total_frames and j in duplicate_indices:
                    j += 1
                if j >= total_frames:
                    break  # trailing run, already handled above
                gap_len = gap_lengths[i]
                img_a = Image.fromarray(cv2.cvtColor(all_frames[i], cv2.COLOR_BGR2RGB))
                img_b = Image.fromarray(cv2.cvtColor(all_frames[j], cv2.COLOR_BGR2RGB))
                for k in range(1, gap_len + 1):
                    t = k / (gap_len + 1)
                    out_img = rife.process(img_a, img_b, timestep=t)
                    out_bgr = cv2.cvtColor(np.array(out_img), cv2.COLOR_RGB2BGR)
                    write_frame(out_bgr)

        produced = sorted(frames_out.glob("*.png"))
        if on_line:
            on_line(f"RIFE produced {len(produced)} frames "
                     f"(original had {total_frames}). Re-encoding at "
                     f"{target_fps:.3f} fps...")

        reassemble_cmd = [
            ffmpeg, "-y",
            "-framerate", str(target_fps), "-i", str(frames_out / "%08d.png"),
            "-i", str(input_path),
            "-map", "0:v", "-map", "1:a?",
            "-c:v", codec,
        ]
        if codec == "prores_ks":
            reassemble_cmd += ["-profile:v", "3", "-pix_fmt", "yuv422p10le"]
        elif codec in ("libx264", "libx265"):
            reassemble_cmd += ["-crf", str(crf), "-pix_fmt", "yuv420p"]
        reassemble_cmd += ["-c:a", "copy", "-shortest", str(output_path)]

        run(reassemble_cmd, on_line=on_line)

    return info, target_fps, summary
