#!/usr/bin/env python3
"""
Universal Telegram Bot CLI & Service
Zero external dependencies (uses Python standard library and system curl).

Works globally from any directory without needing paid-review-research.
Configuration loaded from:
  1. CLI arguments (--token, --chat-id)
  2. Environment variables (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
  3. ~/.config/telegram/bot.env
  4. ~/paid-review-research/.env
"""

import argparse
import html
import json
import mimetypes
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# Optimize network by preferring IPv4 (prevents IPv6 unreachable connection timeouts)
_orig_getaddrinfo = socket.getaddrinfo
def _getaddrinfo_v4(*args, **kwargs):
    responses = _orig_getaddrinfo(*args, **kwargs)
    v4 = [r for r in responses if r[0] == socket.AF_INET]
    return v4 if v4 else responses
socket.getaddrinfo = _getaddrinfo_v4


GLOBAL_ENV_FILE = os.path.expanduser("~/.config/telegram/bot.env")
LEGACY_ENV_FILE = os.path.expanduser("~/paid-review-research/.env")


def load_env_file(filepath):
    """Parse simple key=value environment file."""
    env = {}
    if os.path.isfile(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip().strip("'\"")
        except Exception:
            pass
    return env


def get_credentials(token=None, chat_id=None):
    """Resolve token and chat_id across CLI args, env vars, and config files."""
    resolved_token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    resolved_chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    if not resolved_token or not resolved_chat_id:
        cfg = load_env_file(GLOBAL_ENV_FILE)
        resolved_token = resolved_token or cfg.get("TELEGRAM_BOT_TOKEN")
        resolved_chat_id = resolved_chat_id or cfg.get("TELEGRAM_CHAT_ID")

    if not resolved_token or not resolved_chat_id:
        cfg = load_env_file(LEGACY_ENV_FILE)
        resolved_token = resolved_token or cfg.get("TELEGRAM_BOT_TOKEN")
        resolved_chat_id = resolved_chat_id or cfg.get("TELEGRAM_CHAT_ID")

    return (resolved_token or "").strip(), (resolved_chat_id or "").strip()


def save_credentials(token, chat_id):
    """Save credentials to universal config file ~/.config/telegram/bot.env."""
    os.makedirs(os.path.dirname(GLOBAL_ENV_FILE), exist_ok=True)
    lines = [
        "# Universal Telegram Bot Configuration\n",
        f"TELEGRAM_BOT_TOKEN={token}\n",
        f"TELEGRAM_CHAT_ID={chat_id}\n",
    ]
    with open(GLOBAL_ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.chmod(GLOBAL_ENV_FILE, 0o600)
    print(f" Saved credentials to {GLOBAL_ENV_FILE}")


def send_message(text, token=None, chat_id=None, parse_mode="HTML"):
    """Send text message with automatic splitting for long texts."""
    bot_token, target_chat = get_credentials(token, chat_id)
    if not bot_token or not target_chat:
        print(" Error: Telegram bot token or chat ID is missing.", file=sys.stderr)
        return False

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    max_len = 4000
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] if text else [""]

    success = True
    for chunk in chunks:
        payload = {
            "chat_id": target_chat,
            "text": chunk,
            "disable_web_page_preview": False,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            api_url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "UniversalTelegramBot/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                if not resp_data.get("ok"):
                    print(f" Telegram API error: {resp_data}", file=sys.stderr)
                    success = False
        except Exception as e:
            print(f" Failed to send message: {e}", file=sys.stderr)
            success = False

    return success


def send_document(filepath, caption="", token=None, chat_id=None, parse_mode="HTML"):
    """
    Send file document. Uses curl -4 for blazing-fast multipart streaming,
    falling back to standard urllib if curl is unavailable.
    """
    bot_token, target_chat = get_credentials(token, chat_id)
    if not bot_token or not target_chat:
        print(" Error: Telegram bot token or chat ID is missing.", file=sys.stderr)
        return False

    if not os.path.isfile(filepath):
        print(f" Error: File not found: {filepath}", file=sys.stderr)
        return False

    file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
    if file_size_mb > 50:
        print(f" Error: File size {file_size_mb:.1f} MB exceeds Telegram Bot limit (50 MB).", file=sys.stderr)
        return False

    api_url = f"https://api.telegram.org/bot{bot_token}/sendDocument"

    # Fast path: curl with IPv4
    if shutil.which("curl"):
        cmd = [
            "curl", "-4", "-s",
            "-F", f"chat_id={target_chat}",
            "-F", f"document=@{os.path.abspath(filepath)}",
        ]
        if caption:
            cmd.extend(["-F", f"caption={caption}"])
        if parse_mode:
            cmd.extend(["-F", f"parse_mode={parse_mode}"])
        cmd.append(api_url)

        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            try:
                resp_data = json.loads(res.stdout)
                if resp_data.get("ok"):
                    return True
                else:
                    print(f" Telegram API error: {resp_data}", file=sys.stderr)
                    return False
            except Exception:
                pass

    # Fallback: pure python urllib multipart
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    filename = os.path.basename(filepath)
    mime_type = mimetypes.guess_type(filepath)[0] or "application/octet-stream"

    with open(filepath, "rb") as f:
        file_bytes = f.read()

    body_parts = []
    body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{target_chat}\r\n".encode("utf-8"))
    if caption:
        body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode("utf-8"))
        if parse_mode:
            body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"parse_mode\"\r\n\r\n{parse_mode}\r\n".encode("utf-8"))
    header = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"document\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8")
    body_parts.append(header)
    body_parts.append(file_bytes)
    body_parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    full_body = b"".join(body_parts)
    req = urllib.request.Request(
        api_url,
        data=full_body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "UniversalTelegramBot/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            return bool(resp_data.get("ok"))
    except Exception as e:
        print(f" Failed to send document: {e}", file=sys.stderr)
        return False


def get_bot_info(token=None):
    """Retrieve bot profile info (/getMe)."""
    bot_token, _ = get_credentials(token=token)
    if not bot_token:
        return None
    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "UniversalTelegramBot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok"):
                return data.get("result")
    except Exception as e:
        print(f" Error fetching bot info: {e}", file=sys.stderr)
    return None


def get_updates(offset=None, token=None, timeout=20):
    """Long-poll getUpdates."""
    bot_token, _ = get_credentials(token=token)
    if not bot_token:
        return []
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates?timeout={timeout}"
    if offset:
        url += f"&offset={offset}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "UniversalTelegramBot/1.0"})
        with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok"):
                return data.get("result", [])
    except Exception:
        pass
    return []


def download_telegram_file(file_id, dest_path, token=None):
    """Download a file sent to the bot by file_id."""
    bot_token, _ = get_credentials(token=token)
    if not bot_token:
        return False, "No bot token configured."

    quoted_file_id = urllib.parse.quote(str(file_id).strip())
    url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={quoted_file_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "UniversalTelegramBot/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not data.get("ok"):
                desc = data.get("description", "Unknown Telegram error")
                print(f" Telegram getFile error: {desc}", file=sys.stderr)
                return False, desc
            file_path = data["result"]["file_path"]

        download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
        # Use curl -4 for fast and reliable download
        cmd = ["curl", "-4", "-s", "-f", "-o", dest_path, download_url]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode == 0 and os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0:
            return True, None

        # Fallback to urllib
        urllib.request.urlretrieve(download_url, dest_path)
        return True, None
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        print(f" Telegram getFile HTTP {e.code} error: {err_body}", file=sys.stderr)
        try:
            d = json.loads(err_body)
            desc = d.get("description", f"HTTP {e.code}: {e.reason}")
        except Exception:
            desc = f"HTTP {e.code}: {e.reason}"
        return False, desc
    except Exception as e:
        print(f" Failed downloading Telegram file: {e}", file=sys.stderr)
        return False, str(e)


def run_bot_listener(token=None, chat_id=None):
    """
    On-demand interactive bot listener.
    Listens for incoming messages or documents:
    - If user sends a PDF document: converts to EPUB with Calibre and sends back!
    - If user sends /start, /help, /ping: responds with status.
    """
    bot_token, authorized_chat = get_credentials(token, chat_id)
    info = get_bot_info(bot_token)
    if not info:
        print(" Error: Could not verify bot token.")
        sys.exit(1)

    bot_username = info.get("username", "UnknownBot")
    print(f" Universal Telegram Bot Listener active for @{bot_username}")
    print(f" Authorized Chat ID: {authorized_chat}")
    print(" Ready to receive commands and PDF files to convert to EPUB...\n(Press Ctrl+C to stop)")

    offset = None
    workdir = "/tmp/telegram_pdf2epub"
    os.makedirs(workdir, exist_ok=True)

    while True:
        try:
            updates = get_updates(offset=offset, token=bot_token, timeout=20)
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg:
                    continue

                sender_chat_id = str(msg["chat"]["id"])
                # If specific chat configured, verify sender
                if authorized_chat and sender_chat_id != authorized_chat:
                    continue

                text = msg.get("text", "").strip()
                doc = msg.get("document")

                # Handle document upload (PDF)
                if doc:
                    file_name = doc.get("file_name", "document.pdf")
                    file_id = doc.get("file_id")
                    file_size = doc.get("file_size", 0)
                    size_mb = file_size / (1024 * 1024) if file_size else 0

                    if file_name.lower().endswith(".pdf"):
                        # Telegram Bot API enforces a strict 20 MB limit on getFile
                        if file_size > 20 * 1024 * 1024:
                            send_message(
                                f"⚠️ <b>File exceeds Telegram Bot API download limit:</b>\n"
                                f"<code>{html.escape(file_name)}</code> is <b>{size_mb:.1f} MB</b>, but Telegram restricts bots to files ≤ <b>20 MB</b>.\n\n"
                                f"💡 <b>Quick Solution:</b>\n"
                                f"Upload the PDF to <b>Google Drive</b>, <b>Dropbox</b>, or any cloud platform, make it public or 'Anyone with link', and paste the link here. I will download and convert it directly!",
                                token=bot_token, chat_id=sender_chat_id
                            )
                            continue

                        send_message(
                            f" <i>Received <b>{html.escape(file_name)}</b> ({size_mb:.1f} MB). Converting to EPUB with Calibre (layout preservation enabled)...</i>",
                            token=bot_token, chat_id=sender_chat_id
                        )
                        pdf_path = os.path.join(workdir, f"{int(time.time())}_{file_name}")
                        epub_path = os.path.splitext(pdf_path)[0] + ".epub"

                        success, err = download_telegram_file(file_id, pdf_path, token=bot_token)
                        if success:
                            cmd = [
                                "pdf2epub",
                                pdf_path,
                                "-o", epub_path
                            ]
                            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                            if proc.returncode == 0 and os.path.isfile(epub_path):
                                base_epub = os.path.splitext(file_name)[0] + ".epub"
                                final_epub = os.path.join(workdir, base_epub)
                                os.rename(epub_path, final_epub)
                                send_document(
                                    final_epub,
                                    caption=f" <b>Conversion complete!</b>\n<code>{html.escape(base_epub)}</code>",
                                    token=bot_token,
                                    chat_id=sender_chat_id
                                )
                                # Clean up
                                try:
                                    os.remove(pdf_path)
                                    os.remove(final_epub)
                                except Exception:
                                    pass
                            else:
                                send_message(f" <b>Conversion failed:</b>\n<pre>{html.escape(proc.stderr[:1000] or proc.stdout[:1000])}</pre>", token=bot_token, chat_id=sender_chat_id)
                        else:
                            send_message(
                                f"❌ <b>Failed to download from Telegram:</b> {html.escape(err or 'Unknown error')}\n\n"
                                f"💡 If this file is large, upload it to <b>Google Drive</b> or <b>Dropbox</b> and paste the link right here!",
                                token=bot_token, chat_id=sender_chat_id
                            )
                        continue

                # Handle Cloud URL in text messages
                if text and ("http://" in text or "https://" in text or "drive.google.com" in text or "dropbox.com" in text):
                    import re
                    url_match = re.search(r"https?://[^\s]+", text)
                    if url_match:
                        target_url = url_match.group(0)
                        send_message(
                            f"🔗 <i>Received cloud link:</i>\n<code>{html.escape(target_url)}</code>\n\n"
                            f"<i>Fetching file and converting to EPUB with Calibre...</i>",
                            token=bot_token, chat_id=sender_chat_id
                        )
                        temp_epub = os.path.join(workdir, f"converted_{int(time.time())}.epub")
                        cmd = ["pdf2epub", target_url, "-o", temp_epub]
                        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                        if proc.returncode == 0 and os.path.isfile(temp_epub):
                            send_document(
                                temp_epub,
                                caption="✅ <b>Conversion complete from Cloud URL!</b>",
                                token=bot_token,
                                chat_id=sender_chat_id
                            )
                            try:
                                os.remove(temp_epub)
                            except Exception:
                                pass
                        else:
                            send_message(
                                f"❌ <b>Cloud conversion failed:</b>\n<pre>{html.escape(proc.stderr[:1000] or proc.stdout[:1000])}</pre>",
                                token=bot_token, chat_id=sender_chat_id
                            )
                        continue

                # Handle text commands
                if text in ("/start", "/help"):
                    send_message(
                        " <b>PDF to EPUB Bot</b>\n\n"
                        "Send me any PDF file directly or share a cloud link, and I will convert it into a clean, formatted EPUB book using Calibre and return it right here!\n\n"
                        "Commands:\n"
                        "/ping - Test bot connectivity\n"
                        "/help - Show this guide",
                        token=bot_token, chat_id=sender_chat_id
                    )
                elif text == "/ping":
                    send_message("🏓 <b>Pong!</b> Bot is active and ready.", token=bot_token, chat_id=sender_chat_id)

        except KeyboardInterrupt:
            print("\n Stopping bot listener.")
            break
        except Exception as e:
            print(f" Listener error: {e}", file=sys.stderr)
            time.sleep(2)


def main():
    parser = argparse.ArgumentParser(
        description="Universal Telegram Bot CLI - Send messages, files, and convert PDFs anywhere on demand."
    )
    subparsers = parser.add_subparsers(dest="command", help="Sub-commands")

    # Command: send / msg
    p_send = subparsers.add_parser("send", aliases=["msg"], help="Send a text message")
    p_send.add_argument("text", help="Text to send")
    p_send.add_argument("--token", help="Override Bot Token")
    p_send.add_argument("--chat-id", help="Override Chat ID")
    p_send.add_argument("--plain", action="store_true", help="Send as plain text (default: HTML)")

    # Command: doc / file
    p_file = subparsers.add_parser("doc", aliases=["file"], help="Send a document or file (e.g., EPUB, PDF)")
    p_file.add_argument("filepath", help="Path to file to send")
    p_file.add_argument("--caption", "-c", default="", help="Optional caption for file")
    p_file.add_argument("--token", help="Override Bot Token")
    p_file.add_argument("--chat-id", help="Override Chat ID")

    # Command: status / test
    p_stat = subparsers.add_parser("status", aliases=["test"], help="Check bot connectivity and configuration")
    p_stat.add_argument("--token", help="Override Bot Token")
    p_stat.add_argument("--chat-id", help="Override Chat ID")
    p_stat.add_argument("--ping", action="store_true", help="Send a ping notification to the configured chat")

    # Command: listen / daemon
    p_listen = subparsers.add_parser("listen", aliases=["bot"], help="Start interactive listener (converts uploaded PDFs to EPUB)")
    p_listen.add_argument("--token", help="Override Bot Token")
    p_listen.add_argument("--chat-id", help="Override Chat ID")

    # Command: config
    p_cfg = subparsers.add_parser("config", help="View or save global credentials")
    p_cfg.add_argument("--set-token", help="Save Telegram Bot Token")
    p_cfg.add_argument("--set-chat-id", help="Save Telegram Chat ID")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "config":
        token, chat_id = get_credentials()
        if args.set_token:
            token = args.set_token
        if args.set_chat_id:
            chat_id = args.set_chat_id
        if args.set_token or args.set_chat_id:
            save_credentials(token, chat_id)
        print("Current Telegram Configuration:")
        print(f"  Bot Token: {token[:10]}...{token[-5:] if token else 'None'}")
        print(f"  Chat ID:   {chat_id}")
        return

    if args.command in ("send", "msg"):
        parse_mode = None if args.plain else "HTML"
        ok = send_message(args.text, token=args.token, chat_id=args.chat_id, parse_mode=parse_mode)
        if ok:
            print(" Message sent successfully.")
        else:
            sys.exit(1)

    elif args.command in ("doc", "file"):
        ok = send_document(args.filepath, caption=args.caption, token=args.token, chat_id=args.chat_id)
        if ok:
            print(f" Document sent successfully: {args.filepath}")
        else:
            sys.exit(1)

    elif args.command in ("status", "test"):
        token, chat_id = get_credentials(args.token, args.chat_id)
        print("Checking Telegram Bot Setup...")
        print(f"Config Token: {token[:10]}... (length: {len(token) if token else 0})")
        print(f"Config Chat ID: {chat_id}")
        info = get_bot_info(token)
        if info:
            print(f" Bot Online: @{info.get('username')} ({info.get('first_name')})")
            if args.ping:
                print("Sending test ping to chat...")
                ok = send_message(" <b>Ping from Antigravity</b>: Universal Telegram CLI is operational!", token=token, chat_id=chat_id)
                if ok:
                    print(" Ping sent successfully to Telegram!")
                else:
                    print(" Failed to send ping.")
        else:
            print(" Failed to connect to Telegram Bot API with given token.")

    elif args.command in ("listen", "bot"):
        run_bot_listener(token=args.token, chat_id=args.chat_id)


if __name__ == "__main__":
    main()
