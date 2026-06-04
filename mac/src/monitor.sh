#!/bin/bash

# ==========================================
# HOW TO RUN
# ==========================================
# nohup ./monitor.sh > /dev/null 2>&1 &


# ==========================================
# CONFIGURATION
# ==========================================
# Set your target directory here
TARGET_DIR="$HOME/.openclaw/workspace"

# Where to store the saved screenshots and backups
OUTPUT_DIR="./monitor_output"

# Health check file
HEALTH_FILE="last_update.txt"

DELAY_IN_SECONDS=300

# ==========================================
# INITIALIZATION
# ==========================================
mkdir -p "$OUTPUT_DIR"
LAST_SCREEN=""
LAST_DIR_HASH=""

echo "Starting monitoring script. Press [CTRL+C] to stop."

while true; do
# Generate timestamp for file naming
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

# ------------------------------------------
# 1. SCREENSHOT
# ------------------------------------------
TEMP_SCREEN="$OUTPUT_DIR/temp_screen.png"
# -x takes the screenshot without playing the camera shutter sound
screencapture -x "$TEMP_SCREEN"

if [ -z "$LAST_SCREEN" ]; then
# First run: keep the screenshot
NEW_SCREEN_NAME="$OUTPUT_DIR/screen_$TIMESTAMP.png"
mv "$TEMP_SCREEN" "$NEW_SCREEN_NAME"
LAST_SCREEN="$NEW_SCREEN_NAME"
else
# Compare current screenshot with the last saved one
# Note: macOS occasionally embeds timestamps in PNG metadata. If you get
# false positives, you may need ImageMagick in the future, but cmp works nicely.
if cmp -s "$TEMP_SCREEN" "$LAST_SCREEN"; then
# Files are identical, discard the temporary screenshot
rm "$TEMP_SCREEN"
else
# Files differ, keep the new screenshot
NEW_SCREEN_NAME="$OUTPUT_DIR/screen_$TIMESTAMP.png"
mv "$TEMP_SCREEN" "$NEW_SCREEN_NAME"
LAST_SCREEN="$NEW_SCREEN_NAME"
fi
fi

# ------------------------------------------
# 2. DIRECTORY COMPRESSION & COMPARISON
# ------------------------------------------
if [ -d "$TARGET_DIR" ]; then
# Create a hash of the directory based on file modifications, sizes, and names
# This is highly efficient and avoids the "gzip timestamp" false-positive issue
CURR_DIR_HASH=$(find "$TARGET_DIR" -type f -exec stat -f "%m %z %N" {} + 2>/dev/null | md5)

if [ "$CURR_DIR_HASH" != "$LAST_DIR_HASH" ]; then
# The directory contents changed (or it's the first run), compress and keep
ARCHIVE_NAME="$OUTPUT_DIR/backup_$TIMESTAMP.tar.gz"
# Compress the directory
tar -czf "$ARCHIVE_NAME" -C "$(dirname "$TARGET_DIR")" "$(basename "$TARGET_DIR")" 2>/dev/null
LAST_DIR_HASH="$CURR_DIR_HASH"
fi
else
echo "Warning: Target directory '$TARGET_DIR' does not exist."
fi

# ------------------------------------------
# 3. HEALTH CHECK
# ------------------------------------------
# Overwrite the health check file with the current date/time
date > "$HEALTH_FILE"

# Wait seconds before the next loop
sleep $DELAY_IN_SECONDS
done
