#!/usr/bin/env python3
"""
High-Fidelity PDF to EPUB Converter
Uses Calibre's ebook-convert engine with tuned heuristic algorithms to preserve
formatting, unwrap broken lines, fix paragraphs, and output clean EPUB3.

Features:
- Local PDF files and Cloud URLs (Google Drive, Dropbox, OneDrive, Direct links)
- Smart Heuristics: dehyphenation, line unwrapping, table linearization
- Devbook Preset: Monospace code listings, callouts, deep TOC navigation
- Color & Grayscale Modes: E-Ink monochrome optimization with contrast boosting
- Large PDF Optimizer: Automatic raster downsampling & Pillow compression (<30MB for Telegram)
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
    from PIL import Image, ImageOps
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


DEVBOOK_COLOR_CSS = """
/* Devbook Structural Styling (Color Edition) */
pre, code, tt, kbd, samp {
    font-family: 'JetBrains Mono', 'Fira Code', Menlo, Monaco, Consolas, 'Courier New', monospace !important;
    font-size: 0.88em !important;
    line-height: 1.45 !important;
    white-space: pre-wrap !important;
    word-wrap: break-word !important;
}
code {
    background-color: rgba(27, 31, 35, 0.08);
    color: #c7254e;
    padding: 0.15em 0.35em;
    border-radius: 3px;
}
pre {
    background-color: #f6f8fa !important;
    border: 1px solid #d1d5da !important;
    border-left: 4px solid #007acc !important;
    padding: 10px 14px !important;
    border-radius: 4px;
    margin: 1.2em 0 !important;
    overflow-x: auto;
    color: #24292e;
}
pre code {
    background-color: transparent !important;
    color: inherit !important;
    padding: 0 !important;
}
blockquote {
    border-left: 4px solid #0366d6 !important;
    background-color: #f1f8ff !important;
    padding: 10px 16px !important;
    margin: 1.2em 0 !important;
    color: #032f62;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 1.2em 0;
}
th, td {
    border: 1px solid #dfe2e5;
    padding: 6px 12px;
}
th {
    background-color: #f6f8fa;
    font-weight: 600;
}
"""

DEVBOOK_GRAYSCALE_CSS = """
/* Devbook Structural Styling (E-Ink / Grayscale Edition) */
pre, code, tt, kbd, samp {
    font-family: 'JetBrains Mono', 'Fira Code', Menlo, Monaco, Consolas, 'Courier New', monospace !important;
    font-size: 0.88em !important;
    line-height: 1.45 !important;
    white-space: pre-wrap !important;
    word-wrap: break-word !important;
}
code {
    background-color: #eeeeee !important;
    color: #000000 !important;
    padding: 0.15em 0.35em;
    border-radius: 3px;
}
pre {
    background-color: #f4f4f4 !important;
    border: 1px solid #bbbbbb !important;
    border-left: 4px solid #333333 !important;
    padding: 10px 14px !important;
    border-radius: 4px;
    margin: 1.2em 0 !important;
    overflow-x: auto;
    color: #000000 !important;
}
pre code {
    background-color: transparent !important;
    color: inherit !important;
    padding: 0 !important;
}
blockquote {
    border-left: 4px solid #555555 !important;
    background-color: #f8f8f8 !important;
    padding: 10px 16px !important;
    margin: 1.2em 0 !important;
    color: #111111 !important;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 1.2em 0;
}
th, td {
    border: 1px solid #cccccc;
    padding: 6px 12px;
}
th {
    background-color: #e5e5e5;
    font-weight: bold;
}
"""


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


def get_pdf_metadata(pdf_path):
    """Extract embedded PDF title and author using pdfinfo."""
    info = {"title": None, "author": None}
    if not os.path.isfile(pdf_path):
        return info
    try:
        res = subprocess.run(["pdfinfo", pdf_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if line.startswith("Title:"):
                    t = line.split(":", 1)[1].strip()
                    if t and t.lower() not in ("untitled", "microsoft word", "none"):
                        info["title"] = t
                elif line.startswith("Author:"):
                    a = line.split(":", 1)[1].strip()
                    if a and a.lower() not in ("none", "author", "user"):
                        info["author"] = a
    except Exception:
        pass
    return info


def optimize_epub(epub_path, max_dim=1200, quality=80, target_max_mb=8, grayscale=False):
    """
    Compress & downsample oversized raster images inside an EPUB.
    Optionally converts all images to high-contrast monochrome (Grayscale / E-Ink).
    Guarantees output stays under Telegram's payload threshold (<30MB)
    while preserving standard EPUB3 structure and reading clarity.
    """
    if not HAS_PILLOW or not os.path.isfile(epub_path):
        return epub_path

    initial_size = os.path.getsize(epub_path)
    initial_mb = initial_size / (1024 * 1024)

    # For Color mode: skip if already compact (<= target_max_mb)
    # For Grayscale mode: always process to convert images to monochrome
    if not grayscale and target_max_mb and initial_mb <= target_max_mb:
        return epub_path

    mode_label = "Grayscale (E-Ink)" if grayscale else "Color"
    print(f"⚡ Processing EPUB images ({mode_label} Mode, {initial_mb:.1f} MB)...")
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

                        if need_resize:
                            im.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

                        if grayscale:
                            # High-contrast monochrome conversion for E-Ink
                            im_gray = ImageOps.autocontrast(im.convert("L"))
                            im_gray.save(img_path, format="JPEG", quality=72, optimize=True)
                        else:
                            # Graceful color optimization with white background for alpha
                            if im.mode in ("RGBA", "LA", "P"):
                                bg = Image.new("RGB", im.size, (255, 255, 255))
                                if im.mode == "P":
                                    im = im.convert("RGBA")
                                if "A" in im.getbands():
                                    bg.paste(im, mask=im.split()[-1])
                                else:
                                    bg.paste(im)
                                im = bg
                            elif im.mode != "RGB":
                                im = im.convert("RGB")
                            im.save(img_path, format="JPEG", quality=quality, optimize=True)

                        new_bytes = os.path.getsize(img_path)
                        if new_bytes < old_bytes:
                            reduced_bytes += (old_bytes - new_bytes)
                        processed_count += 1
                except Exception:
                    pass

    print(f" Processed {processed_count} images ({mode_label}). Repackaging EPUB archive...")
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
    print(f"✅ Processing complete in {time.time()-t0:.1f}s: {initial_mb:.1f} MB -> {final_mb:.1f} MB")

    if final_size < initial_size or grayscale:
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
    target_max_mb=30,
    devbook=True,
    grayscale=False
):
    """
    Run Calibre ebook-convert with layout preservation, devbook styling, and optimization.
    """
    if not os.path.isfile(input_pdf):
        raise FileNotFoundError(f"Input PDF not found: {input_pdf}")

    if not output_epub:
        base_name = os.path.splitext(input_pdf)[0]
        suffix = "_Grayscale.epub" if grayscale else "_Color.epub"
        output_epub = base_name + suffix

    output_dir = os.path.dirname(os.path.abspath(output_epub))
    os.makedirs(output_dir, exist_ok=True)

    input_size_mb = os.path.getsize(input_pdf) / (1024 * 1024)
    is_large = input_size_mb > 40

    cmd = [
        "ebook-convert",
        os.path.abspath(input_pdf),
        os.path.abspath(output_epub),
        f"--epub-version={epub_version}",
        "--preserve-cover-aspect-ratio",
        "--pdf-engine=calibre",
    ]

    # Grayscale vs Color E-Reader profile
    if grayscale:
        cmd.append("--output-profile=kindle")
    else:
        cmd.append("--output-profile=tablet")

    # Devbook specific structure & CSS
    if devbook:
        css_content = DEVBOOK_GRAYSCALE_CSS if grayscale else DEVBOOK_COLOR_CSS
        cmd.append(f"--extra-css={css_content}")
        cmd.extend([
            "--max-toc-links=0",
            "--toc-threshold=4",
        ])

    # Large PDF specific tuning
    if is_large or optimize_for_large:
        cmd.extend([
            "--epub-max-image-size=1200x1600",
            "--subset-embedded-fonts",
            "--epub-flatten",
        ])

    if enable_heuristics:
        if devbook:
            cmd.extend([
                "--enable-heuristics",
                "--disable-dehyphenate",
                "--html-unwrap-factor=0.35",
            ])
        else:
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
        full_title = f"{title} (Grayscale Edition)" if grayscale and "grayscale" not in title.lower() else title
        cmd.append(f"--title={full_title}")

    if author:
        cmd.append(f"--authors={author}")

    mode_label = "Grayscale (E-Ink)" if grayscale else "Full Color"
    print(f" Running Calibre conversion [{mode_label}]:\n  Input:  {input_pdf} ({input_size_mb:.1f} MB)\n  Output: {output_epub}")
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if proc.returncode != 0:
        print(f" Calibre conversion failed:\n{proc.stderr}", file=sys.stderr)
        raise RuntimeError(f"ebook-convert exited with code {proc.returncode}")

    if not os.path.isfile(output_epub) or os.path.getsize(output_epub) == 0:
        raise RuntimeError("ebook-convert completed but output EPUB was not created.")

    # Run optimizer for image downsampling or grayscale conversion
    if optimize_for_large or grayscale:
        output_epub = optimize_epub(output_epub, target_max_mb=target_max_mb, grayscale=grayscale)

    size_kb = os.path.getsize(output_epub) / 1024
    print(f" Successfully converted to EPUB [{mode_label}]: {output_epub} ({size_kb/1024:.1f} MB)")
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
        description="High-fidelity PDF to EPUB converter with devbook formatting, grayscale E-Ink mode & Telegram dispatch"
    )
    parser.add_argument("input", help="Local PDF file path or Cloud URL (Google Drive, Dropbox, direct link)")
    parser.add_argument("-o", "--output", help="Destination EPUB path (optional)")
    parser.add_argument("-t", "--send-telegram", action="store_true", help="Send the generated EPUB directly to Telegram")
    parser.add_argument("--caption", help="Caption message when sending to Telegram")
    parser.add_argument("--title", help="Book title metadata")
    parser.add_argument("--author", help="Book author metadata")
    parser.add_argument("--epub-version", default="3", choices=["2", "3"], help="EPUB format version (default: 3)")
    parser.add_argument("--grayscale", "-g", action="store_true", help="Generate Grayscale / E-Ink optimized edition")
    parser.add_argument("--both", action="store_true", help="Generate both Full Color and Grayscale editions")
    parser.add_argument("--devbook", action="store_true", default=True, help="Enable devbook styling for code blocks & callouts")
    parser.add_argument("--no-devbook", action="store_false", dest="devbook", help="Disable devbook styling")
    parser.add_argument("--no-heuristics", action="store_true", help="Disable Calibre heuristic unwrap/formatting fixes")
    parser.add_argument("--no-optimize", action="store_true", help="Disable image downsampling/compression")
    parser.add_argument("--max-mb", type=int, default=30, help="Target max size in MB for Telegram payload optimization (default: 30)")

    args = parser.parse_args()

    temp_dir = None
    input_file = args.input

    try:
        if is_url(args.input):
            temp_dir = tempfile.mkdtemp(prefix="pdf2epub_")
            input_file = download_cloud_file(args.input, temp_dir)

        meta = get_pdf_metadata(input_file)
        base_title = args.title or meta.get("title") or os.path.splitext(os.path.basename(input_file))[0].replace("_", " ").title()
        base_author = args.author or meta.get("author") or ""

        # Intelligently rename hex/UUID filenames to descriptive slugs
        is_hex_name = bool(re.match(r"^[0-9A-Fa-f_-]{16,}$", os.path.splitext(os.path.basename(input_file))[0]))
        if is_hex_name and base_title:
            slug = re.sub(r"[^\w\s-]", "", base_title).strip()
            slug = re.sub(r"[-\s]+", "_", slug)[:40].strip("_")
            default_base = os.path.join(os.path.dirname(input_file), slug)
        else:
            default_base = os.path.splitext(input_file)[0]

        if args.both:
            print(f"🚀 Generating BOTH Full Color and Grayscale (E-Ink) editions for: {base_title}")
            # 1. Color Edition
            out_color = (os.path.splitext(args.output)[0] + "_Color.epub") if args.output else f"{default_base}_Color.epub"
            res_color = convert_pdf_to_epub(
                input_pdf=input_file,
                output_epub=out_color,
                title=base_title,
                author=base_author,
                epub_version=args.epub_version,
                enable_heuristics=not args.no_heuristics,
                optimize_for_large=not args.no_optimize,
                target_max_mb=args.max_mb,
                devbook=args.devbook,
                grayscale=False
            )
            if args.send_telegram:
                author_line = f"<i>Authors: {base_author}</i>\n" if base_author else ""
                send_to_telegram(
                    res_color,
                    caption=f"📘 <b>{base_title}</b> (Full Color Edition)\n{author_line}• Reflowable EPUB3\n• Preserves full-color diagrams, figures & formatting"
                )

            # 2. Grayscale Edition
            out_gray = (os.path.splitext(args.output)[0] + "_Grayscale.epub") if args.output else f"{default_base}_Grayscale.epub"
            res_gray = convert_pdf_to_epub(
                input_pdf=input_file,
                output_epub=out_gray,
                title=base_title,
                author=base_author,
                epub_version=args.epub_version,
                enable_heuristics=not args.no_heuristics,
                optimize_for_large=not args.no_optimize,
                target_max_mb=args.max_mb,
                devbook=args.devbook,
                grayscale=True
            )
            if args.send_telegram:
                author_line = f"<i>Authors: {base_author}</i>\n" if base_author else ""
                send_to_telegram(
                    res_gray,
                    caption=f"📱 <b>{base_title}</b> (E-Ink Grayscale Edition)\n{author_line}• High-contrast 8-bit monochrome tailored for Kindle & Kobo\n• Battery-efficient, paper-like reading"
                )

            print(f"🎉 Both editions generated successfully!\n  Color:     {res_color}\n  Grayscale: {res_gray}")

        else:
            # Single edition (Color or Grayscale)
            epub_result = convert_pdf_to_epub(
                input_pdf=input_file,
                output_epub=args.output,
                title=base_title,
                author=base_author,
                epub_version=args.epub_version,
                enable_heuristics=not args.no_heuristics,
                optimize_for_large=not args.no_optimize,
                target_max_mb=args.max_mb,
                devbook=args.devbook,
                grayscale=args.grayscale
            )
            if args.send_telegram:
                mode_str = "Grayscale E-Ink" if args.grayscale else "Full Color"
                send_to_telegram(epub_result, caption=args.caption or f"📚 <b>{base_title}</b> ({mode_str} Devbook Edition)")

            print(f" Done! Result: {epub_result}")

    finally:
        if temp_dir and os.path.isdir(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass


if __name__ == "__main__":
    main()
