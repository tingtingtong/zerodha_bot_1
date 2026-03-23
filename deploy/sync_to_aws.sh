#!/bin/bash
# Run this from your LOCAL Windows machine (Git Bash or WSL)
# Syncs bot code to AWS EC2
# Usage: bash deploy/sync_to_aws.sh <your-ec2-ip> <path-to-pem>
# Example: bash deploy/sync_to_aws.sh 13.234.56.78 ~/zerodhabot.pem

EC2_IP=$1
PEM=$2

if [ -z "$EC2_IP" ] || [ -z "$PEM" ]; then
    echo "Usage: bash deploy/sync_to_aws.sh <ec2-ip> <pem-file>"
    exit 1
fi

echo "Syncing code to EC2 at $EC2_IP ..."

rsync -avz \
    --exclude '.env' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.cache' \
    --exclude 'journaling/logs' \
    --exclude 'reporting/output' \
    --exclude 'backtesting/results' \
    --exclude 'config/.zerodha_token.json' \
    -e "ssh -i $PEM" \
    ./ ubuntu@$EC2_IP:~/zerodhaBot/

# Copy .env separately (excluded from rsync above for safety)
echo "Copying .env ..."
scp -i "$PEM" .env ubuntu@$EC2_IP:~/zerodhaBot/.env

echo ""
echo "Sync complete. Connect with:"
echo "  ssh -i $PEM ubuntu@$EC2_IP"
