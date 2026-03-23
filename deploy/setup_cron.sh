#!/bin/bash
# Sets up cron job to run bot Monday-Friday at 8:45 AM IST (3:15 UTC)
# Run once after setup_aws.sh

chmod +x ~/zerodhaBot/deploy/start_bot.sh

# Add cron entry (Mon-Fri at 03:15 UTC = 08:45 AM IST)
CRON_ENTRY="15 3 * * 1-5 /bin/bash ~/zerodhaBot/deploy/start_bot.sh"

# Remove existing entry if any, then add fresh
(crontab -l 2>/dev/null | grep -v "start_bot.sh"; echo "$CRON_ENTRY") | crontab -

echo "Cron job set:"
crontab -l
echo ""
echo "Bot will auto-start Mon-Fri at 08:45 AM IST."
