# Raspberry Pi & Google Drive Setup Guide

This guide explains how to deploy WeatherBot on a headless Raspberry Pi to run 24/7, while seamlessly syncing your `data/` folder (containing trading history and config) to Google Drive. This ensures both your laptop and your Pi share the exact same JSON state files instantly.

## 1. Prerequisites
SSH into your Raspberry Pi and ensure your system is updated with the required tools:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install git python3 python3-venv tmux rclone -y
```

## 2. Authenticate Google Drive (`rclone`)
Since your Pi does not have a web browser, we use `rclone` to authenticate headlessly.

1. Run the configuration wizard:
   ```bash
   rclone config
   ```
2. Type `n` for **New remote** and name it `gdrive`.
3. Select **Google Drive** from the list of storage providers (usually option `18`).
4. Leave the Client ID / Secret blank (hit Enter).
5. Choose `1` for Full Access.
6. Skip advanced config (hit Enter).
7. When asked `Use auto config?`, type `n`. 
8. `rclone` will give you a command to run on your laptop's terminal. Doing so will open your laptop's web browser to log into Google. Paste the resulting token back into the Pi's terminal. 

## 3. Clone the WeatherBot Repository
Get your fork of the bot onto the Pi:
```bash
git clone <your-github-repo-url>
cd weatherbot
```

## 4. Setup Python Environment
Create the virtual environment and install the required dependencies:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install rich  # Required for the TUI dashboard
```
*Note: Don't forget to recreate your `.env` file on the Pi with your `VISUAL_CROSSING_KEY`, since Git automatically ignores it!*

## 5. Mount Google Drive and Symlink Data
Now we will mount the Google Drive to the Pi and secretly map the bot's `data/` folder to it. Zero code changes are needed!

1. Create a mount point and a folder for the bot on your Google Drive:
   ```bash
   mkdir -p ~/google_drive
   rclone mount gdrive: ~/google_drive --vfs-cache-mode writes --daemon
   mkdir -p ~/google_drive/WeatherBotData
   ```

2. Inside your `weatherbot` repository folder, swap the local data directory with a shortcut to the cloud:
   ```bash
   mv data/* ~/google_drive/WeatherBotData/
   rm -rf data
   ln -s ~/google_drive/WeatherBotData data
   ```

## 6. Run 24/7 with `tmux`
We use `tmux` (a terminal multiplexer) to keep the bot alive in the background even when you close your SSH connection.

1. **Start a new background session** named "weatherbot":
   ```bash
   tmux new -s weatherbot
   ```
2. **Start the bot**:
   ```bash
   source .venv/bin/activate
   python bot_v2.py
   ```
3. **Detach and leave it running**:
   Press **`Ctrl+B`**, release, then press **`D`**. 

You are returned to your normal Pi terminal. The bot is safely running inside the hidden `tmux` session, and all data is instantly saved to Google Drive.

*Tip: To check the bot's live logs later, SSH into your Pi and run `tmux attach -t weatherbot`.*
