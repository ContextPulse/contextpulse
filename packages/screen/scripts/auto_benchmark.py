"""Automated storage benchmark: opens different content types and captures/classifies each.

No user interaction needed — just run and wait for the report.
Opens URLs in Chrome, waits for load, captures, classifies, closes tab.
"""

import json
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from contextpulse_sight import capture
from contextpulse_sight.classifier import classify_and_extract
from contextpulse_sight.privacy import get_foreground_process_name, get_foreground_window_title

OUTPUT_DIR = Path(__file__).parent.parent / "benchmark_results"

# Test scenarios: (label, action_type, target, wait_seconds)
SCENARIOS = [
    # Websites - mixed content types
    ("Amazon homepage", "url", "https://www.amazon.com", 5),
    ("Reddit front page", "url", "https://www.reddit.com", 5),
    ("CNN news", "url", "https://www.cnn.com", 5),
    ("Wikipedia article", "url", "https://en.wikipedia.org/wiki/Artificial_intelligence", 5),
    ("Google Maps", "url", "https://www.google.com/maps", 5),
    ("YouTube video", "url", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", 6),
    ("GitHub code", "url", "https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/_client.py", 5),
    ("Google Docs blank", "url", "https://docs.google.com", 4),
    # Local apps
    ("File Explorer", "shell", "explorer C:\\Users\\david\\Projects", 3),
    ("Notepad empty", "shell", "notepad", 3),
    # Current state (whatever is on screen right now)
    ("Current screen", "none", None, 0),
]


def open_url(url):
    """Open URL in default browser, return process handle."""
    return subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)


def open_app(cmd):
    """Open a local app."""
    return subprocess.Popen(cmd, shell=True)


def close_foreground():
    """Send Alt+F4 to close foreground window."""
    # Post Alt+F4 keystrokes
    subprocess.run(
        ["powershell", "-Command",
         "(New-Object -ComObject WScript.Shell).SendKeys('%{F4}')"],
        capture_output=True, timeout=5,
    )


def capture_and_classify(label):
    """Capture active monitor, run OCR, return analysis dict."""
    idx, img = capture.capture_active_monitor()

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=75)
    img_size = len(buf.getvalue())

    result = classify_and_extract(img)
    text = result.get("text", "") or ""
    text_size = len(text.encode("utf-8"))

    return {
        "label": label,
        "timestamp": time.time(),
        "time_str": time.strftime("%H:%M:%S"),
        "monitor": idx,
        "window_title": get_foreground_window_title()[:120],
        "app_name": get_foreground_process_name(),
        "img_width": img.width,
        "img_height": img.height,
        "img_bytes": img_size,
        "ocr_type": result["type"],
        "ocr_chars": result.get("chars", 0),
        "ocr_lines": result.get("lines", 0),
        "ocr_confidence": round(result.get("confidence", 0), 3),
        "ocr_time_s": round(result.get("ocr_time", 0), 2),
        "text_bytes": text_size,
        "savings_pct": round((1 - text_size / img_size) * 100, 1) if text_size > 0 and img_size > 0 else 0,
        "recommendation": "TEXT_ONLY" if result["type"] == "text" else "KEEP_IMAGE",
        "sample_text": text[:300] if text else "",
    }


def print_report(results):
    total = len(results)
    text_only = sum(1 for r in results if r["recommendation"] == "TEXT_ONLY")
    keep_image = total - text_only

    total_img = sum(r["img_bytes"] for r in results)
    smart_bytes = sum(
        r["text_bytes"] if r["recommendation"] == "TEXT_ONLY" else r["img_bytes"]
        for r in results
    )
    savings = total_img - smart_bytes

    print("\n" + "=" * 80)
    print("AUTOMATED STORAGE BENCHMARK REPORT")
    print("=" * 80)
    print(f"\nScenarios tested: {total}")
    print(f"  Text-only eligible: {text_only} ({text_only/total*100:.0f}%)")
    print(f"  Image required:     {keep_image} ({keep_image/total*100:.0f}%)")
    print("\nDisk usage:")
    print(f"  All images:  {total_img/1024:.0f} KB")
    print(f"  Smart mode:  {smart_bytes/1024:.0f} KB")
    print(f"  Savings:     {savings/1024:.0f} KB ({savings/total_img*100:.0f}%)" if total_img else "")

    print(f"\n{'Label':<25} {'Type':<11} {'Chars':>6} {'Conf':>6} {'ImgKB':>6} {'TxtKB':>6} {'App':<20}")
    print("-" * 90)
    for r in results:
        print(
            f"{r['label']:<25} "
            f"{r['recommendation']:<11} "
            f"{r['ocr_chars']:>6} "
            f"{r['ocr_confidence']:>6.2f} "
            f"{r['img_bytes']//1024:>6} "
            f"{r['text_bytes']//1024:>6} "
            f"{r['app_name'][:20]:<20}"
        )

    # Confidence distribution
    print("\nConfidence by content type:")
    for r in results:
        bar_len = int(r["ocr_confidence"] * 30)
        bar = "#" * bar_len + "." * (30 - bar_len)
        icon = "TXT" if r["recommendation"] == "TEXT_ONLY" else "IMG"
        print(f"  [{icon}] {r['ocr_confidence']:.2f} |{bar}| {r['label']}")

    # Text samples
    print("\nText samples (first 150 chars):")
    for r in results:
        if r["sample_text"]:
            snippet = r["sample_text"][:150].replace("\n", " ")
            print(f"  [{r['label']}]: {snippet}")
        else:
            print(f"  [{r['label']}]: (no text captured)")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    print("=" * 50)
    print("ContextPulse Auto-Benchmark")
    print("=" * 50)
    print(f"\nRunning {len(SCENARIOS)} scenarios automatically.")
    print("Sit back — this takes about 90 seconds.\n")

    for label, action_type, target, wait in SCENARIOS:
        print(f"  [{time.strftime('%H:%M:%S')}] Testing: {label}...", end=" ", flush=True)

        proc = None
        try:
            if action_type == "url":
                proc = open_url(target)
                time.sleep(wait)
            elif action_type == "shell":
                proc = open_app(target)
                time.sleep(wait)
            else:
                time.sleep(0.5)

            r = capture_and_classify(label)
            results.append(r)

            icon = "TXT" if r["recommendation"] == "TEXT_ONLY" else "IMG"
            print(
                f"[{icon}] {r['ocr_chars']:>5} chars @ {r['ocr_confidence']:.2f} "
                f"| {r['img_bytes']//1024:>3}KB vs {r['text_bytes']//1024:>3}KB"
            )

            # Close the window we opened (except "current screen")
            if action_type in ("url", "shell"):
                time.sleep(0.5)
                close_foreground()
                time.sleep(1)

        except Exception as e:
            print(f"ERROR: {e}")

    # Save results
    output_file = OUTPUT_DIR / f"auto_benchmark_{time.strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nRaw data saved to: {output_file}")

    print_report(results)


if __name__ == "__main__":
    main()
