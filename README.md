# Transactions_Tracker_based_on_SMS
Script will use the backup SMS file which is an x animal and tracks the transactions and financial data for a particular person from particular bank and updates in Google sheet

#Features and process to setup the env. to run the script.

Automated SMS Transaction Logger (Termux + Google Sheets)
An automated Python pipeline designed to run natively inside Termux on Android. It parses transaction SMS XML backup files (e.g., from SMS Backup & Restore), normalizes transaction details and contact VPAs, deduplicates incoming records, cleans older backup files, and synchronizes transactions into monthly tabs in Google Sheets with real-time push notifications.
📋 Features
 * Automatic XML Cleanup: Retains only the latest .xml backup file in the working folder and deletes older historical backups.
 * Smart Contact & VPA Mapping: Normalizes phone numbers, UPI VPAs, and shop names into readable custom labels (e.g., mapping SUNILKINNERA123@AXL or 9494966515 to DAD).
 * Robust Deduplication: Prevents duplicate entries using a 1-second time window and exact UPI reference match logic.
 * Dynamic Monthly Sheets: Automatically routes transactions to appropriate monthly tabs (e.g., AUGUST2026).
 * Termux Automation: Background execution via cron paired with native Android status bar notifications using termux-notification.
🛠️ Prerequisites & Requirements
1. Applications Required on Android
> ⚠️ Important: Download Termux apps from F-Droid, not the Google Play Store (which is unmaintained).
> 
 * Termux (F-Droid)
 * Termux:API (F-Droid)
 * Termux:Boot (F-Droid) (Optional, for auto-start after phone reboots)
 * SMS Backup & Restore (or any app generating standard SMS .xml backups)
2. Android App Permissions
Go to Settings \rightarrow Apps \rightarrow Termux:
 * Grant Storage and SMS permissions.
 * Set Battery Optimization to Unrestricted / Don't Optimize.
🔑 Google Cloud Service Account Setup
To allow your Python script to update your Google Sheet automatically:
 * Go to the Google Cloud Console.
 * Create a new project (or select an existing one).
 * Navigate to APIs & Services \rightarrow Library:
   * Search for and enable Google Sheets API.
   * Search for and enable Google Drive API.
 * Navigate to IAM & Admin \rightarrow Service Accounts:
   * Click + Create Service Account.
   * Enter a name (e.g., googlesheetautomation).
   * Copy the generated service account email (e.g., googlesheetautomation@your-project-id.iam.gserviceaccount.com).
 * Create a key:
   * Select your service account \rightarrow Keys tab \rightarrow Add Key \rightarrow Create new key.
   * Select JSON and download the file.
 * Share your Google Sheet:
   * Open your target Google Sheet in a browser.
   * Click Share and add your service account email address as an Editor.
📂 File Directory Structure
Set up the directory structure in Termux as follows:
/data/data/com.termux/files/home/Script_for_SMS/
├── fifth-composite-489705-v5-d06baf7f990f.json  # Google Cloud Service Account Key
├── sms_sync.py                                 # Main Python Sync Script
└── sms-20261024.xml                            # Latest SMS Backup File

🚀 Step-by-Step Installation Guide
Step 1: Initialize Termux & Grant Storage Access
Open Termux and run:
# Update repository packages
pkg update && pkg upgrade -y

# Request Android storage access
termux-setup-storage

(Accept the storage permission prompt on your device)
Step 2: Install System Dependencies & Build Tools
Install required system binaries and native dependencies:
pkg install python termux-api cronie clang rust make libffi openssl openssl-tool python-cryptography -y

Step 3: Install Required Python Libraries
pip install gspread oauth2client requests

Step 4: Create Directory & Place Files
 * Create the target script directory:
   mkdir -p ~/Script_for_SMS

 * Copy your downloaded Google Cloud JSON credentials file to the script directory:
   cp /sdcard/Download/*.json ~/Script_for_SMS/

 * Save your main Python code into ~/Script_for_SMS/sms_sync.py:
   nano ~/Script_for_SMS/sms_sync.py

   (Paste the Python script into nano, press CTRL + O, Enter to save, and CTRL + X to exit)
 * Copy your latest SMS backup .xml file into the folder:
   cp /sdcard/Download/*.xml ~/Script_for_SMS/

Step 5: Test Script Manually
Run the script manually to confirm authentication, spreadsheet updates, file cleanup, and notifications:
python ~/Script_for_SMS/sms_sync.py

⏰ Automating with Cron Jobs
1. Configure the Cron Schedule
To automatically pull new SMS backups from your Download folder and execute the sync script every 30 minutes (or on a custom schedule):
Open the crontab editor:
crontab -e

Add your scheduled rule at the bottom:
# Copy fresh XML backup from Downloads and execute SMS sync every 30 minutes
*/30 * * * * cp /sdcard/Download/*.xml /data/data/com.termux/files/home/Script_for_SMS/ 2>/dev/null; /data/data/com.termux/files/usr/bin/python /data/data/com.termux/files/home/Script_for_SMS/sms_sync.py >> /data/data/com.termux/files/home/Script_for_SMS/cron.log 2>&1

Save and exit (CTRL + O \rightarrow Enter \rightarrow CTRL + X).
2. Start the Cron Daemon & Acquire Wake Lock
# Prevent Android CPU from sleeping
termux-wake-lock

# Start background cron service
crond

3. Verify Background Execution
To confirm cron is running and check execution history:
# View current cron jobs
crontab -l

# Read output & error logs
cat ~/Script_for_SMS/cron.log

🛠️ Maintenance & Troubleshooting
 * ValueError: time data '...' does not match format: Caused by corrupted or shifted cells in your Google Sheet (e.g., text like ONLINE sitting in the time column or full dates in column A). Correct or remove the malformed row in the sheet and re-run.
 * Unable to find the server at oauth2.googleapis.com: Indicates a temporary network disconnect or DNS lookup failure. Verify internet connectivity.
 * Cron Service Stopped: If your phone restarts, re-run crond and termux-wake-lock, or use Termux:Boot to run crond automatically on system startup.

