"""Storage mode benchmark: captures your screen every few seconds,
runs OCR on each frame, and reports which would be text-only vs image.

Usage:
    python scripts/benchmark_storage.py

    Then open different apps/websites during the capture window.
    When done, press Ctrl+C to stop and see the report.

Suggested test sequence (open each for ~10 seconds):
    1. This terminal (code/text)
    2. A website like Amazon or Reddit (mixed text + images)
    3. Google Maps or a photo gallery (visual)
    4. VS Code with a file open (code)
    5. Gmail or Outlook (email text)
    6. A YouTube video (visual)
    7. File Explorer (UI elements)
    8. A PDF document
    9. Discord or Slack (chat)
    10. Windows desktop (icons only)
"""

import json
import sys
import time
from io import BytesIO
from pathlib import Path

# Add the package to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from contextpulse_sight import capture
from contextpulse_sight.classifier import classify_and_extract
from contextpulse_sight.privacy import get_foreground_window_title, get_foreground_process_name

INTERVAL = 3  # seconds between captures
OUTPUT_DIR = Path(__file__).parent.parent / "benchmark_results"


def capture_and_classify():
    """Capture active monitor, run OCR, return analysis dict."""
    idx, img = capture.capture_active_monitor()

    # Measure image size
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=75)
    img_size = len(buf.getvalue())

    # Run OCR at downscaled resolution (same as buffer would store)
    result = classify_and_extract(img)

    text_size = len(result.get("text", "") or "") if result.get("text") else 0

    return {
        "timestamp": time.time(),
        "time_str": time.strftime("%H:%M:%S"),
        "monitor": idx,
        "window_title": get_foreground_window_title()[:100],
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
    }


def print_report(results):
    """Print summary report of all captures."""
    total = len(results)
    text_only = sum(1 for r in results if r["recommendation"] == "TEXT_ONLY")
    keep_image = total - text_only

    total_img_bytes = sum(r["img_bytes"] for r in results)
    text_only_bytes = sum(r["text_bytes"] for r in results if r["recommendation"] == "TEXT_ONLY")
    kept_img_bytes = sum(r["img_bytes"] for r in results if r["recommendation"] == "KEEP_IMAGE")
    smart_total = text_only_bytes + kept_img_bytes
    savings = total_img_bytes - smart_total

    print("\n" + "=" * 70)
    print("STORAGE BENCHMARK REPORT")
    print("=" * 70)
    print(f"\nTotal captures: {total}")
    print(f"  Text-only eligible: {text_only} ({text_only/total*100:.0f}%)")
    print(f"  Image required:     {keep_image} ({keep_image/total*100:.0f}%)")

    print(f"\nDisk usage comparison:")
    print(f"  All images (visual mode):  {total_img_bytes/1024:.0f} KB")
    print(f"  Smart mode:                {smart_total/1024:.0f} KB")
    print(f"  Savings:                   {savings/1024:.0f} KB ({savings/total_img_bytes*100:.0f}%)")

    # Group by app
    apps = {}
    for r in results:
        app = r["app_name"] or "unknown"
        if app not in apps:
            apps[app] = {"text": 0, "image": 0, "samples": []}
        if r["recommendation"] == "TEXT_ONLY":
            apps[app]["text"] += 1
        else:
            apps[app]["image"] += 1
        apps[app]["samples"].append(r)

    print(f"\nPer-app breakdown:")
    print(f"  {'App':<25} {'Text-only':>10} {'Image':>10} {'Avg Conf':>10} {'Avg Chars':>10}")
    print(f"  {'-'*25} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
    for app, data in sorted(apps.items(), key=lambda x: -len(x[1]["samples"])):
        avg_conf = sum(s["ocr_confidence"] for s in data["samples"]) / len(data["samples"])
        avg_chars = sum(s["ocr_chars"] for s in data["samples"]) / len(data["samples"])
        print(f"  {app:<25} {data['text']:>10} {data['image']:>10} {avg_conf:>10.2f} {avg_chars:>10.0f}")

    # Show OCR confidence distribution
    print(f"\nOCR confidence distribution:")
    conf_buckets = {"0.0-0.5": 0, "0.5-0.7": 0, "0.7-0.8": 0, "0.8-0.9": 0, "0.9-1.0": 0}
    for r in results:
        c = r["ocr_confidence"]
        if c < 0.5:
            conf_buckets["0.0-0.5"] += 1
        elif c < 0.7:
            conf_buckets["0.5-0.7"] += 1
        elif c < 0.8:
            conf_buckets["0.7-0.8"] += 1
        elif c < 0.9:
            conf_buckets["0.8-0.9"] += 1
        else:
            conf_buckets["0.9-1.0"] += 1
    for bucket, count in conf_buckets.items():
        bar = "#" * (count * 2)
        print(f"  {bucket}: {count:>3} {bar}")

    # Show individual captures
    print(f"\nDetailed capture log:")
    print(f"  {'Time':<10} {'App':<20} {'Type':<10} {'Chars':>6} {'Conf':>6} {'ImgKB':>6} {'TxtKB':>6} {'Window'}")
    print(f"  {'-'*10} {'-'*20} {'-'*10} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*40}")
    for r in results:
        print(
            f"  {r['time_str']:<10} "
            f"{r['app_name'][:20]:<20} "
            f"{r['recommendation']:<10} "
            f"{r['ocr_chars']:>6} "
            f"{r['ocr_confidence']:>6.2f} "
            f"{r['img_bytes']//1024:>6} "
            f"{r['text_bytes']//1024:>6} "
            f"{r['window_title'][:40]}"
        )


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 50)
    print("ContextPulse Storage Benchmark")
    print("=" * 50)
    print(f"\nCapturing every {INTERVAL}s. Open different apps to test.")
    print("Press Ctrl+C to stop and see the report.\n")
    print("Suggested sequence:")
    print("  1. Terminal/code editor (text)")
    print("  2. Website like Amazon (mixed)")
    print("  3. Google Maps / photos (visual)")
    print("  4. Email / chat app (text)")
    print("  5. File Explorer (UI)")
    print("  6. PDF / document (text)")
    print("  7. Desktop with just icons (visual)")
    print()

    results = []
    try:
        while True:
            try:
                r = capture_and_classify()
                results.append(r)
                icon = "📝" if r["recommendation"] == "TEXT_ONLY" else "🖼️"
                print(
                    f"  [{r['time_str']}] {icon} {r['recommendation']:<10} "
                    f"| {r['ocr_chars']:>5} chars @ {r['ocr_confidence']:.2f} "
                    f"| {r['img_bytes']//1024:>3}KB img vs {r['text_bytes']//1024:>3}KB txt "
                    f"| {r['app_name'][:15]} — {r['window_title'][:40]}"
                )
            except Exception as e:
                print(f"  [ERROR] {e}")
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        pass

    if not results:
        print("\nNo captures taken.")
        return

    # Save raw data
    output_file = OUTPUT_DIR / f"benchmark_{time.strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nRaw data saved to: {output_file}")

    print_report(results)


if __name__ == "__main__":
    main()
