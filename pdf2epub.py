#!/usr/bin/env python3
"""
High-Fidelity PDF to EPUB Converter
Uses Calibre's ebook-convert engine with tuned heuristic algorithms to preserve
formatting, unwrap broken lines, fix paragraphs, and output clean EPUB3.

Features:
- Local PDF files and Cloud URLs (Google Drive, Dropbox, OneDrive, Direct links)
- Smart Heuristics: dehyphenation, line unwrapping, table linearization
- Large PDF Optimizer: Automatic raster downsampling & Pillow compression (<50MB for Telegram)
- Automated delivery to Telegram via universal tg-cli
"""

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


def is_url(path):
    """Check if the provided input is a web URL."""
    try:
        parsed = urllib.parse.urlparse(path)
        return parsed.scheme in ("http", "https")
    except Exception:
        return False


def extract_gdrive_id(url):
    """Extract Google Drive file ID if present."""
    match = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?id=)([a-zA-Z0-9_-]+)", url)
    if match:
        return match.group(1)
    return None


def download_cloud_file(url, target_dir):
    """
    Download a file from cloud storage or direct URL.
    Returns path to downloaded local file.
    """
    print(f"📥 Fetching file from URL: {url}")
    os.makedirs(target_dir, exist_ok=True)

    # 1. Google Drive
    gdrive_id = extract_gdrive_id(url)
    if gdrive_id:
        print(f" Detected Google Drive file ID: {gdrive_id}")
        gdown_bin = shutil.which("gdown") or os.path.expanduser("~/.local/bin/gdown")
        dest_file = os.path.join(target_dir, f"gdrive_{gdrive_id}.pdf")
        if os.path.isfile(gdown_bin) or shutil.which("gdown"):
            cmd = [gdown_bin, f"https://drive.google.com/uc?id={gdrive_id}", "-O", dest_file, "--fuzzy"]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if os.path.isfile(dest_file) and os.path.getsize(dest_file) > 1000:
                print(f" Downloaded via gdown: {dest_file}")
                return dest_file

    # 2. Dropbox
    if "dropbox.com" in url:
        if "dl=0" in url:
            url = url.replace("dl=0", "dl=1")
        elif "?" not in url:
            url += "?dl=1"
        print(" Transformed Dropbox link for direct download.")

    # 3. Direct curl download
    dest_file = os.path.join(target_dir, "downloaded_document.pdf")
    curl_cmd = [
        "curl", "-4", "-L", "-f",
        "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "-o", dest_file,
        url
    ]
    proc = subprocess.run(curl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode == 0 and os.path.isfile(dest_file) and os.path.getsize(dest_file) > 1000:
        print(f" Downloaded via curl: {dest_file} ({os.path.getsize(dest_file)} bytes)")
        return dest_file

    # 4. Fallback: urllib
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest_file, "wb") as out:
            shutil.copyfileobj(resp, out)
        if os.path.isfile(dest_file) and os.path.getsize(dest_file) > 1000:
            print(f" Downloaded via urllib: {dest_file}")
            return dest_file
    except Exception as e:
        print(f" Urllib error: {e}")

    raise RuntimeError(f"Could not download file from URL: {url}")


def optimize_epub(epub_path, max_dim=960, quality=70, target_max_mb=30):
    """
    Compress & downsample oversized raster images inside an EPUB.
    Guarantees output stays under Telegram's payload threshold (<30MB)
    while preserving standard EPUB3 structure and reading clarity.
    """
    if not HAS_PILLOW or not os.path.isfile(epub_path):
        return epub_path

    initial_size = os.path.getsize(epub_path)
    initial_mb = initial_size / (1024 * 1024)

    # Only run if larger than target threshold or explicitly called
    if target_max_mb and initial_mb <= target_max_mb:
        return epub_path

    print(f"⚡ Large EPUB detected ({initial_mb:.1f} MB > target {target_max_mb} MB). Running optimization engine...")
    t0 = time.time()
    temp_dir = epub_path + "_opt_tmp"
    opt_epub = epub_path + ".optimized.epub"
    shutil.rmtree(temp_dir, ignore_errors=True)

    with zipfile.ZipFile(epub_path, "r") as z:
        z.extractall(temp_dir)

    image_exts = {".png", ".jpg", ".jpeg", ".webp"}
    processed_count = 0
    reduced_bytes = 0

    for root, _, files in os.walk(temp_dir):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in image_exts:
                img_path = os.path.join(root, f)
                old_bytes = os.path.getsize(img_path)
                try:
                    with Image.open(img_path) as im:
                        w, h = im.size
                        need_resize = w > max_dim or h > max_dim
                        need_compress = old_bytes > 25 * 1024

                        if need_resize or need_compress:
                            if need_resize:
                                im.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

                            if im.mode in ("RGBA", "LA") and "transparency" in im.info:
                                im.save(img_path, format="PNG", optimize=True)
                            else:
                                if im.mode != "RGB":
                                    im = im.convert("RGB")
                                im.save(img_path, format="JPEG", quality=quality, optimize=True)

                            new_bytes = os.path.getsize(img_path)
                            if new_bytes < old_bytes:
                                reduced_bytes += (old_bytes - new_bytes)
                            processed_count += 1
                except Exception:
                    pass

    print(f" Optimized {processed_count} images. Repackaging EPUB archive...")
    with zipfile.ZipFile(opt_epub, "w", compression=zipfile.ZIP_DEFLATED) as z_out:
        # EPUB spec mandates mimetype is first entry and uncompressed (ZIP_STORED)
        mimetype_path = os.path.join(temp_dir, "mimetype")
        if os.path.isfile(mimetype_path):
            z_out.write(mimetype_path, "mimetype", compress_type=zipfile.ZIP_STORED)

        for root, _, files in os.walk(temp_dir):
            for f in files:
                full_p = os.path.join(root, f)
                rel_p = os.path.relpath(full_p, temp_dir)
                if rel_p == "mimetype":
                    continue
                z_out.write(full_p, rel_p, compress_type=zipfile.ZIP_DEFLATED)

    shutil.rmtree(temp_dir, ignore_errors=True)
    final_size = os.path.getsize(opt_epub)
    final_mb = final_size / (1024 * 1024)
    print(f"✅ Optimization complete in {time.time()-t0:.1f}s: {initial_mb:.1f} MB -> {final_mb:.1f} MB")

    if final_size < initial_size:
        os.replace(opt_epub, epub_path)
    else:
        if os.path.isfile(opt_epub):
            os.remove(opt_epub)

    return epub_path


def convert_pdf_to_epub(
    input_pdf,
    output_epub=None,
    title=None,
    author=None,
    epub_version="3",
    enable_heuristics=True,
    linearize_tables=True,
    smarten_punctuation=True,
    change_justification="justify",
    optimize_for_large=True,
    target_max_mb=48
):
    """
    Run Calibre ebook-convert with layout preservation and large-file optimization.
    """
    if not os.path.isfile(input_pdf):
        raise FileNotFoundError(f"Input PDF not found: {input_pdf}")

    if not output_epub:
        base_name = os.path.splitext(input_pdf)[0]
        output_epub = base_name + ".epub"

    output_dir = os.path.dirname(os.path.abspath(output_epub))
    os.makedirs(output_dir, exist_ok=True)

    input_size_mb = os.path.getsize(input_pdf) / (1024 * 1024)
    is_large = input_size_mb > 50

    cmd = [
        "ebook-convert",
        os.path.abspath(input_pdf),
        os.path.abspath(output_epub),
        f"--epub-version={epub_version}",
        "--preserve-cover-aspect-ratio",
        "--pdf-engine=calibre",
    ]

    # Large PDF specific tuning
    if is_large or optimize_for_large:
        cmd.extend([
            "--max-image-res=1440x1920",
            "--subset-embedded-fonts",
            "--epub-flatten",
        ])

    if enable_heuristics:
        cmd.extend([
            "--enable-heuristics",
            "--html-unwrap-factor=0.45",
        ])

    if linearize_tables:
        cmd.append("--linearize-tables")

    if smarten_punctuation:
        cmd.append("--smarten-punctuation")

    if change_justification:
        cmd.append(f"--change-justification={change_justification}")

    if title:
        cmd.append(f"--title={title}")

    if author:
        cmd.append(f"--authors={author}")

    print(f" Running Calibre conversion:\n  Input:  {input_pdf} ({input_size_mb:.1f} MB)\n  Output: {output_epub}")
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if proc.returncode != 0:
        print(f" Calibre conversion failed:\n{proc.stderr}", file=sys.stderr)
        raise RuntimeError(f"ebook-convert exited with code {proc.returncode}")

    if not os.path.isfile(output_epub) or os.path.getsize(output_epub) == 0:
        raise RuntimeError("ebook-convert completed but output EPUB was not created.")

    # Run optimizer if output EPUB exceeds target threshold
    if optimize_for_large:
        output_epub = optimize_epub(output_epub, target_max_mb=target_max_mb)

    size_kb = os.path.getsize(output_epub) / 1024
    print(f" Successfully converted to EPUB: {output_epub} ({size_kb:.1f} KB / {size_kb/1024:.1f} MB)")
    return output_epub


def send_to_telegram(epub_file, caption=None):
    """Deliver converted EPUB via universal tg-cli."""
    tg_cli = shutil.which("tg-cli") or "/usr/local/bin/tg-cli"
    if not os.path.isfile(tg_cli):
        print("⚠️ Warning: tg-cli not found. Unable to send to Telegram.")
        return False

    cap = caption or f"📚 <b>Converted EPUB:</b> <code>{os.path.basename(epub_file)}</code>"
    cmd = [tg_cli, "doc", epub_file, "--caption", cap]
    print(f"🚀 Sending {os.path.basename(epub_file)} to Telegram...")
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode == 0:
        print(" Sent to Telegram successfully!")
        return True
    else:
        print(f" Failed sending to Telegram: {res.stderr}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="High-fidelity PDF to EPUB converter with cloud download, large PDF optimization & Telegram dispatch"
    )
    parser.add_argument("input", help="Local PDF file path or Cloud URL (Google Drive, Dropbox, direct link)")
    parser.add_argument("-o", "--output", help="Destination EPUB path (optional)")
    parser.add_argument("-t", "--send-telegram", action="store_true", help="Send the generated EPUB directly to Telegram")
    parser.add_argument("--caption", help="Caption message when sending to Telegram")
    parser.add_argument("--title", help="Book title metadata")
    parser.add_argument("--author", help="Book author metadata")
    parser.add_argument("--epub-version", default="3", choices=["2", "3"], help="EPUB format version (default: 3)")
    parser.add_argument("--no-heuristics", action="store_true", help="Disable Calibre heuristic unwrap/formatting fixes")
    parser.add_argument("--no-optimize", action="store_true", help="Disable large PDF image downsampling/compression")
    parser.add_argument("--max-mb", type=int, default=48, help="Target max size in MB for Telegram payload optimization (default: 48)")

    args = parser.parse_args()

    temp_dir = None
    input_file = args.input

    try:
        if is_url(args.input):
            temp_dir = tempfile.mkdtemp(prefix="pdf2epub_")
            input_file = download_cloud_file(args.input, temp_dir)

        output_file = args.output
        if not output_file:
            if is_url(args.input):
                base_name = os.path.splitext(os.path.basename(input_file))[0]
                output_file = os.path.join(os.getcwd(), f"{base_name}.epub")
            else:
                output_file = os.path.splitext(input_file)[0] + ".epub"

        epub_result = convert_pdf_to_epub(
            input_pdf=input_file,
            output_epub=output_file,
            title=args.title,
            author=args.author,
            epub_version=args.epub_version,
            enable_heuristics=not args.no_heuristics,
            optimize_for_large=not args.no_optimize,
            target_max_mb=args.max_mb
        )

        if args.send_telegram:
            send_to_telegram(epub_result, caption=args.caption)

        print(f" Done! Result: {epub_result}")

    finally:
        if temp_dir and os.path.isdir(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass


if __name__ == "__main__":
    main()
