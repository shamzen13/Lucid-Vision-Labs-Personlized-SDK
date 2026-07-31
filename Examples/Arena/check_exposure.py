#!/usr/bin/env python3
"""
check_exposure.py

Quick exposure/bit-depth sanity check for raw SWIR/NIR captures.

Usage:
    python check_exposure.py path/to/frame.png
    python check_exposure.py path/to/frame.tiff --low 1 --high 99

What it does:
  1. Loads the image WITHOUT any colormap or auto-scaling (so you see the
     actual sensor values, not a display-stretched version).
  2. Reports bit depth, min/max/mean, and how full the dynamic range is.
  3. Plots a histogram of raw pixel values.
  4. Produces a percentile-stretched preview (for your eyes only --
     never use this stretched version for quantitative analysis, only
     to judge exposure / composition).
  5. Saves both plots next to the input file.

Notes:
  - PNG only supports 8-bit or 16-bit. If your capture pipeline is saving
    Mono16 from the camera but the PNG comes back looking 8-bit (max value
    stuck at 255), that's a sign the save step is downcasting -- check
    that step, not the camera.
  - For anything you'll actually measure (FWHM, radial decay, spectral
    ratios), work from the untouched array this script loads, not the
    stretched preview.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def load_raw(path: Path) -> np.ndarray:
    img = Image.open(path)
    mode = img.mode
    arr = np.array(img)

    if arr.ndim == 3:
        # If it's a "PNG that looks like a color image" but is really a
        # single-channel capture that got saved as RGB, flag it.
        if np.allclose(arr[..., 0], arr[..., 1]) and np.allclose(arr[..., 1], arr[..., 2]):
            print(f"[note] Image has 3 channels but they're identical -- "
                  f"treating as single-channel (mode was {mode}).")
            arr = arr[..., 0]
        else:
            print(f"[warning] Image has {arr.shape[-1]} channels with "
                  f"differing values (mode={mode}). This looks like it may "
                  f"already be colormapped (e.g. jet). Histogram/exposure "
                  f"stats below will still run per-channel-averaged, but "
                  f"you should re-export the RAW single-channel data from "
                  f"your capture pipeline for real analysis.")
            arr = arr.mean(axis=-1)

    return arr.astype(np.float64), mode


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", type=str, help="Path to raw capture (PNG/TIFF)")
    ap.add_argument("--low", type=float, default=1.0,
                     help="Lower percentile for stretch preview (default: 1)")
    ap.add_argument("--high", type=float, default=99.0,
                     help="Upper percentile for stretch preview (default: 99)")
    ap.add_argument("--out", type=str, default=None,
                     help="Output prefix for saved plots (default: alongside input)")
    args = ap.parse_args()

    path = Path(args.image)
    if not path.exists():
        print(f"Error: file not found: {path}")
        sys.exit(1)

    arr, mode = load_raw(path)

    # --- Bit depth / range diagnostics ---
    vmin, vmax, vmean, vstd = arr.min(), arr.max(), arr.mean(), arr.std()
    # Use the 99.9th percentile instead of the raw max so a handful of
    # hot/dead pixels (common on InGaAs sensors) don't masquerade as real
    # saturated signal.
    v_robust_high = np.percentile(arr, 99.9)

    if mode in ("I", "I;16", "I;16B", "I;16L") or vmax > 255:
        assumed_bits = 16
        full_scale = 65535
    else:
        assumed_bits = 8
        full_scale = 255

    n_hot = int(np.sum(arr >= 0.99 * full_scale))
    fill_pct = 100.0 * v_robust_high / full_scale

    print("=" * 60)
    print(f"File:            {path.name}")
    print(f"PIL mode:        {mode}")
    print(f"Assumed depth:   {assumed_bits}-bit (full scale = {full_scale})")
    print(f"Min / Max:       {vmin:.1f} / {vmax:.1f}")
    print(f"Mean / Std:      {vmean:.1f} / {vstd:.1f}")
    print(f"99.9th pctile:   {v_robust_high:.1f}  <- used for exposure check")
    print(f"Hot pixels (>=99% of scale): {n_hot}")
    print(f"Robust peak fill: {fill_pct:.1f}% of full-scale range")
    print("=" * 60)
    if n_hot > 0 and n_hot < arr.size * 0.001:
        print(f"[note] {n_hot} pixel(s) sit near full scale but are a tiny "
              f"fraction of the image -- likely hot/dead pixels, not real "
              f"saturated signal. Exposure judgment below uses the 99.9th "
              f"percentile instead, which ignores these outliers.")

    if assumed_bits == 8:
        print("[flag] This looks like 8-bit data. If you intended to capture "
              "Mono16 from the camera, something in your save pipeline is "
              "downcasting -- check the PixelFormat node and your save/export "
              "code, not just the camera settings.")
    if fill_pct < 20:
        print(f"[flag] Peak signal only reaches {fill_pct:.1f}% of full scale. "
              f"You have headroom -- increase exposure time and/or gain, and "
              f"re-check the light source coupling (reflector type, working "
              f"distance, aperture).")
    elif fill_pct > 98:
        print("[flag] Signal is at or near saturation. Reduce exposure/gain "
              "slightly to avoid clipping highlights.")
    else:
        print("[ok] Peak signal is in a reasonable range -- not badly under- "
              "or over-exposed.")

    # --- Histogram ---
    out_prefix = Path(args.out) if args.out else path.with_suffix("")
    hist_path = f"{out_prefix}_histogram.png"
    preview_path = f"{out_prefix}_stretched_preview.png"

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(arr.ravel(), bins=200, color="steelblue")
    ax.set_yscale("log")
    ax.set_xlabel("Raw pixel value")
    ax.set_ylabel("Count (log scale)")
    ax.set_title(f"Raw histogram -- {path.name}")
    ax.axvline(vmax, color="red", linestyle="--", linewidth=1, label=f"max={vmax:.0f}")
    ax.legend()
    fig.tight_layout()
    fig.savefig(hist_path, dpi=150)
    plt.close(fig)
    print(f"Saved histogram to: {hist_path}")

    # --- Percentile stretch preview (viewing only) ---
    lo, hi = np.percentile(arr, [args.low, args.high])
    stretched = np.clip((arr - lo) / max(hi - lo, 1e-6), 0, 1)

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].imshow(arr, cmap="gray")
    axes[0].set_title("Raw (as-is display)")
    axes[0].axis("off")
    axes[1].imshow(stretched, cmap="gray")
    axes[1].set_title(f"Percentile-stretched ({args.low}-{args.high}%) -- VIEWING ONLY")
    axes[1].axis("off")
    fig.tight_layout()
    fig.savefig(preview_path, dpi=150)
    plt.close(fig)
    print(f"Saved stretched preview to: {preview_path}")
    print("\nReminder: use the stretched preview to judge composition/exposure "
          "only. For FWHM, radial decay, or any quantitative feature "
          "extraction, always work from the raw array, never the stretched "
          "or colormapped version.")


if __name__ == "__main__":
    main()
