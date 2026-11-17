#!/usr/bin/env python3
"""
High-Fidelity PDF to EPUB Converter
Uses Calibre's ebook-convert engine with tuned heuristic algorithms to preserve
formatting, unwrap broken lines, fix paragraphs, and output clean EPUB3.

Supports:
- Local PDF files
- Cloud storage URLs (Google Drive, Dropbox, OneDrive, Direct URLs)
- Automated delivery to Telegram via universal tg-cli
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request


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

    # 1. Check for Google Drive
    gdrive_id = extract_gdrive_id(url)
    if gdrive_id:
        print(f" Detected Google Drive file ID: {gdrive_id}")
        gdown_bin = shutil.which("gdown") or os.path.expanduser("~/.local/bin/gdown")
        dest_file = os.path.join(target_dir, f"gdrive_{gdrive_id}.pdf")
        if os.path.isfile(gdown_bin) or shutil.which("gdown"):
            cmd = [gdown_bin, f"https://drive.google.com/uc?id={gdrive_id}", "-O", dest_file, "--fuzzy"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if os.path.isfile(dest_file) and os.path.getsize(dest_file) > 1000:
                print(f" Downloaded via gdown: {dest_file}")
                return dest_file
            print(f"⚠️ gdown output: {res.stdout} {res.stderr}")

    # 2. Check for Dropbox
    if "dropbox.com" in url:
        if "dl=0" in url:
            url = url.replace("dl=0", "dl=1")
        elif "?" not in url:
            url += "?dl=1"
        print(" Transformed Dropbox link for direct download.")

    # 3. Direct curl download
    dest_file = os.path.join(target_dir, "downloaded_document.pdf")
    curl_cmd = [
        "curl", "-L", "-f",
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


def convert_pdf_to_epub(
    input_pdf,
    output_epub=None,
    title=None,
    author=None,
    epub_version="3",
    enable_heuristics=True,
    linearize_tables=True,
    smarten_punctuation=True,
    change_justification="justify"
):
    """
    Run Calibre ebook-convert with layout preservation options.
    """
    if not os.path.isfile(input_pdf):
        raise FileNotFoundError(f"Input PDF not found: {input_pdf}")

    if not output_epub:
        base_name = os.path.splitext(input_pdf)[0]
        output_epub = base_name + ".epub"

    output_dir = os.path.dirname(os.path.abspath(output_epub))
    os.makedirs(output_dir, exist_ok=True)

    cmd = [
        "ebook-convert",
        os.path.abspath(input_pdf),
        os.path.abspath(output_epub),
        f"--epub-version={epub_version}",
        "--preserve-cover-aspect-ratio",
        "--pdf-engine=calibre",
    ]

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

    print(f" Running Calibre conversion:\n  Input:  {input_pdf}\n  Output: {output_epub}")
    start_time = os.times()
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if proc.returncode != 0:
        print(f" Calibre conversion failed:\n{proc.stderr}", file=sys.stderr)
        raise RuntimeError(f"ebook-convert exited with code {proc.returncode}")

    if not os.path.isfile(output_epub) or os.path.getsize(output_epub) == 0:
        raise RuntimeError("ebook-convert completed but output EPUB was not created.")

    size_kb = os.path.getsize(output_epub) / 1024
    print(f" Successfully converted to EPUB: {output_epub} ({size_kb:.1f} KB)")
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
        description="High-fidelity PDF to EPUB converter with cloud download & Telegram dispatch"
    )
    parser.add_argument("input", help="Local PDF file path or Cloud URL (Google Drive, Dropbox, direct link)")
    parser.add_argument("-o", "--output", help="Destination EPUB path (optional)")
    parser.add_argument("-t", "--send-telegram", action="store_true", help="Send the generated EPUB directly to Telegram")
    parser.add_argument("--caption", help="Caption message when sending to Telegram")
    parser.add_argument("--title", help="Book title metadata")
    parser.add_argument("--author", help="Book author metadata")
    parser.add_argument("--epub-version", default="3", choices=["2", "3"], help="EPUB format version (default: 3)")
    parser.add_argument("--no-heuristics", action="store_true", help="Disable Calibre heuristic unwrap/formatting fixes")

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
