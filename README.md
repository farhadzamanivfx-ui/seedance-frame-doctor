# Seedance Frame Doctor

A free Windows tool that detects and fixes the **duplicate/held-frame stutter** baked into Seedance 2.0 generated clips.

---

## The Problem

Seedance 2.0 outputs often have held/duplicate frames baked in — commonly every 3rd or 4th frame — causing visible stutter and jump when scrubbing, especially in fast-action shots. This is a known, community-reported issue:


---

## What It Does

**Analyze** → Scans your clip and reports exactly how many frames are duplicated and whether they follow a repeating pattern (e.g. "every 4th frame").

Uses a **snap-aware detector** that distinguishes Seedance's held-frame artifact from genuine slow motion — so a locked-off shot at the end of your clip won't get falsely flagged.

**Fix** → Removes precisely those flagged frames, then fills each gap with real interpolated in-between frames — preserving the original frame count and timing. Three interpolation engines:

| Mode | Description | When to use |
|---|---|---|
| **blend** | ffmpeg cross-blend of the two real bordering frames | Safest, no structural artifacts, slight ghost in fast motion |
| **mci** | ffmpeg motion-compensated interpolation | Sharper, but can tear at hair/occlusion edges |
| **rife** | AI optical-flow (RIFE v4.6 model) | Best quality for VFX work, handles fine detail and occlusion well |

---

## Download

👉 **[Download SeedanceFrameDoctor.exe](../../releases/latest)**

Single `.exe` for Windows — ffmpeg and the RIFE AI model are bundled inside. End users need nothing else installed.

---

## Building from Source

**Prerequisites:** Python 3.x installed (any recent version, the script handles the rest).

1. Download and unzip this repository
2. Run `setup_and_build.bat`

That's it. The script will:
- Create an isolated virtual environment (keeps this tool's packages completely separate from your global Python / ComfyUI install — avoids DLL conflicts)
- Install all Python dependencies inside the venv
- Download ffmpeg from gyan.dev (~100 MB, first run only, skipped if already present)
- Install the RIFE AI interpolation engine from the bundled `vendor/` folder (no download needed for Python 3.11 / Windows 64-bit; falls back to PyPI for other versions)
- Build `dist\SeedanceFrameDoctor.exe` — a single self-contained file

The `.venv` folder created during the build is a build tool only — it does not need to be shipped.

---

## Running from Source (no build needed)

```bash
pip install opencv-python numpy pillow rife-ncnn-vulkan-python-tntwise
```

**Analyze a clip:**
```
python cli.py report --input clip.mp4
```

**Fix a clip (default: blend mode, ProRes HQ output):**
```
python cli.py fix --input clip.mp4 --output clip_fixed.mov
```

**Fix with AI interpolation:**
```
python cli.py fix --input clip.mp4 --output clip_fixed.mov --interp-mode rife
```

---

## Parameters

### Detection (used by both Analyze and Fix)

| Parameter | Default | Description |
|---|---|---|
| `--threshold` | `2.0` | Duplicate detection sensitivity (0–255 grayscale scale). A frame is flagged only if (a) its pixel diff from the previous frame is below this value AND (b) the next frame "snaps back" with a larger diff — this snap test distinguishes Seedance artifact from genuine slow motion. Raise to 3–4 if under-detecting; lower to 1.0 if over-detecting on slow-motion shots. |
| `--downscale` | `320` | Width used for diff comparison (frames are resized for speed). Rarely needs changing. |

### Fix-only

| Parameter | Default | Description |
|---|---|---|
| `--fps` | source fps | Output frame rate. Leave blank to match source. |
| `--codec` | `prores_ks` | Output codec. `prores_ks` = ProRes 422 HQ (10-bit, for VFX/comp ingestion). Use `libx264`/`libx265` for lightweight review files. |
| `--interp-mode` | `blend` | Interpolation engine: `blend`, `mci`, or `rife`. |
| `--model` | auto | (rife only) RIFE model variant name, e.g. `rife-v4.6`. Auto-picks best available. |
| `--gpu` | auto | (rife only) GPU id: `0` = first GPU, `-1` = CPU only. |

---

## How It Works

1. **Frame-by-frame diff** — Each frame is compared to the previous one (downscaled grayscale, for speed) using mean absolute pixel difference.
2. **Snap-aware detection** — A frame is flagged as a held duplicate only if its diff is below `threshold` AND the next frame's diff is significantly larger (the "snap back" to real motion). This prevents natural slow motion at the end of a shot from being mistakenly flagged.
3. **Exact removal** — The flagged frames are removed using ffmpeg's `select` filter — the exact frames the Analyze step found, not a separate algorithm with different thresholds.
4. **Per-gap interpolation** — For each removed frame, one real interpolated frame is generated at the exact time position of the gap (not a global redistribution). With RIFE, this is a full AI optical-flow in-between; with blend/mci, it uses ffmpeg's minterpolate.
5. **Re-encode** — Output at the original fps, original frame count, in your chosen codec.

---

## Project Structure

```
core.py              # All detection and fix logic (no UI)
cli.py               # Command-line front-end
gui_app.py           # Tkinter desktop GUI
setup_and_build.bat  # One-button build script (only needs Python)
build_exe.bat        # Rebuild-only script (if venv already exists)
vendor/              # Pre-downloaded RIFE wheel + model weights
```

---

## Licensing

© 2026 Farhad Zamani. All rights reserved.

This tool is free to use for personal and commercial VFX/post-production work. Redistribution of the source code or built executable, with or without modification, requires attribution to the original author.

Third-party components:
- ffmpeg: GPLv3 (invoked as a separate process, not linked) — https://ffmpeg.org
- RIFE model: MIT — https://github.com/nihui/rife-ncnn-vulkan
- rife-ncnn-vulkan-python: MIT — https://github.com/TNTwise/rife-ncnn-vulkan-python
