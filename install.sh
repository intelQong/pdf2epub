#!/usr/bin/env bash
#
# Universal PDF to EPUB & Telegram Bot Installer
# Installs Calibre, sets up global CLI commands, and configures Telegram bot service.
#

set -e

echo "=========================================================="
echo "  Universal PDF to EPUB & Telegram Bot Setup"
echo "=========================================================="

# 1. Check prerequisites
echo "[1/5] Checking package manager & installing dependencies..."
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y calibre curl python3
elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y calibre curl python3
elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm calibre curl python
else
    echo "⚠️ Unsupported package manager. Please ensure Calibre and Python 3 are installed."
fi

# 2. Install CLI tools to /usr/local/bin
echo "[2/5] Installing CLI utilities into /usr/local/bin..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sudo cp "${SCRIPT_DIR}/tg_cli.py" /usr/local/bin/tg-cli
sudo chmod +x /usr/local/bin/tg-cli
sudo ln -sf /usr/local/bin/tg-cli /usr/local/bin/tg-send
sudo ln -sf /usr/local/bin/tg-cli /usr/local/bin/telegram-send

sudo cp "${SCRIPT_DIR}/pdf2epub.py" /usr/local/bin/pdf2epub
sudo chmod +x /usr/local/bin/pdf2epub

# 3. Set up configuration directory
echo "[3/5] Setting up ~/.config/telegram/..."
mkdir -p "$HOME/.config/telegram"
chmod 700 "$HOME/.config/telegram"

if [ ! -f "$HOME/.config/telegram/bot.env" ]; then
    if [ -f "${SCRIPT_DIR}/.env.example" ]; then
        cp "${SCRIPT_DIR}/.env.example" "$HOME/.config/telegram/bot.env"
        chmod 600 "$HOME/.config/telegram/bot.env"
        echo " Created ~/.config/telegram/bot.env template. Please populate it with your token."
    fi
fi

# 4. Add environment loader to ~/.bashrc if needed
echo "[4/5] Updating shell configuration..."
if [ -f "$HOME/.bashrc" ] && ! grep -q "TELEGRAM_BOT_TOKEN" "$HOME/.bashrc"; then
    echo -e '\n# Telegram Bot Environment Variables\nif [ -f "$HOME/.config/telegram/bot.env" ]; then\n  set -a\n  . "$HOME/.config/telegram/bot.env"\n  set +a\nfi' >> "$HOME/.bashrc"
    echo " Added environment sourcing to ~/.bashrc"
fi

# 5. Set up systemd user service
echo "[5/5] Configuring systemd user service..."
mkdir -p "$HOME/.config/systemd/user"
cp "${SCRIPT_DIR}/systemd/tg-pdf-bot.service" "$HOME/.config/systemd/user/tg-bot.service"

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload || true
    systemctl --user enable tg-bot.service || true
    echo " Service tg-bot.service enabled."
    echo " To start the bot listener now, run: systemctl --user start tg-bot.service"
fi

echo "=========================================================="
echo "✅ Installation complete!"
echo "  Run 'pdf2epub --help' to convert PDFs."
echo "  Run 'tg-cli status' to verify Telegram bot connection."
echo "=========================================================="
