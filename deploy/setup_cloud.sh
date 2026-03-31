#!/bin/bash
# ============================================================
# ZerodhaBot — Free Cloud Setup Script
# Works on: Oracle Cloud Free Tier, Google Cloud e2-micro
# Run once on a fresh Ubuntu 22.04 VM
#
# Usage:
#   git clone <your-repo> ~/zerodhaBot
#   cd ~/zerodhaBot
#   bash deploy/setup_cloud.sh
# ============================================================
set -e

echo "======================================"
echo "  ZerodhaBot Cloud Setup"
echo "======================================"

# 1. System packages
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv git curl tzdata

# 2. Set timezone to IST
sudo timedatectl set-timezone Asia/Kolkata
echo "Timezone set to: $(timedatectl | grep 'Time zone')"

# 3. Python virtual environment
cd ~/zerodhaBot
python3 -m venv venv
source venv/bin/activate

# 4. Install lightweight cloud requirements (no Playwright, no Streamlit)
pip install --upgrade pip
pip install -r requirements_cloud.txt

echo ""
echo "======================================"
echo "  Dependencies installed."
echo ""
echo "  Next steps:"
echo "  1. Copy your .env file:"
echo "     nano ~/zerodhaBot/.env"
echo ""
echo "  2. Set Telegram tokens in .env:"
echo "     TELEGRAM_BOT_TOKEN=your_token"
echo "     TELEGRAM_CHAT_ID=your_chat_id"
echo ""
echo "  3. Set up cron to auto-start:"
echo "     bash deploy/setup_cron_cloud.sh"
echo ""
echo "  4. Test run manually:"
echo "     bash deploy/start_paper.sh"
echo "======================================"
