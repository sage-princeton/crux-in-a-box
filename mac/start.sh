#!/usr/bin/env bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# ====== DEPENDENCIES ======
# install Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# show hidden files - helps when looking for directories like .openclaw
defaults write com.apple.finder AppleShowAllFiles -bool true


# ====== OPENCLAW ======
# install openclaw (no onboarding)
curl -fsSL https://openclaw.ai/install.sh | bash -s -- --no-onboard


# ====== MONITORING ======
cp $SCRIPT_DIR/src/monitor.sh ~
nohup ~/monitor.sh > /dev/null 2>&1 &


# ====== WALLPAPER ======
# sets the wallpaper to a solid color to not confuse the monitoring script
# by default, macs seem to have moving wallpapers
osascript -e 'tell application "Finder" to set desktop picture to POSIX file "/System/Library/Desktop Pictures/Solid Colors/Yellow.png"'


# ====== TELEMETRY ======
git clone https://github.com/schwartzadev/openclaw-telemetry-hal
curl -fsSL https://get.pnpm.io/install.sh | sh -
source /Users/administrator/.zshrc
pnpm install
pnpm run build
openclaw plugins install --link .
# TODO: update the openclaw.json file's json itself
openclaw gateway restart

# ====== UNRESTRICTED ACCESS ======
# TODO: add the part about overrides

# ====== SERVICES ======

# install GitHub
brew install gh

# install AWS
curl "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o "AWSCLIV2.pkg"
sudo installer -pkg AWSCLIV2.pkg -target /

# install gogcli
brew install openclaw/tap/gogcli

# ======

# # Log in to GitHub
# gh auth login

# # Check GitHub status
# gh auth status

# Set up gog
# see: https://gogcli.sh/quickstart.html
