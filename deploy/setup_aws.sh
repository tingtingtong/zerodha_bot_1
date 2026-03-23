#!/bin/bash
# Run this once on a fresh AWS EC2 Ubuntu instance
# Usage: bash setup_aws.sh

set -e
echo "=============================="
echo "  ZerodhaBot AWS Setup"
echo "=============================="

# System packages
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv git unzip curl

# Create bot directory
mkdir -p ~/zerodhaBot
cd ~/zerodhaBot

# Python virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Install Playwright Chromium (for auto-login)
playwright install chromium
playwright install-deps chromium

echo ""
echo "=============================="
echo "  Setup complete!"
echo "  Next: copy your .env file"
echo "  Then run: bash deploy/start_bot.sh"
echo "=============================="
