# UniFi Automated Consoles Backup (Redux)

A modernized Flask dashboard for managing UniFi console backups with scheduling, logging, and manual triggers.

## Highlights
- Real-time dashboard with live status updates (SSE).
- Automated scheduled backups and connectivity checks.
- Manual backup triggers, download of latest backups, and 30-day history view.
- Docker-friendly structure with persistent storage.

## Project Layout
```
.
├── UniFi_Automated_Consoles_Backup.py   # Backward-compatible entrypoint
├── unifi_backup_app/                   # Modularized application package
│   ├── templates/                      # Jinja2 templates
│   ├── static/                         # CSS/JS assets
│   └── ...
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

## Local Python Run (no Docker)
> Make sure Chrome is installed locally if you use manual login.

```bash
python UniFi_Automated_Consoles_Backup.py
```

Then open: http://localhost:5000

## Docker Desktop (Windows) – PowerShell Commands
> Use your local folder `C:\Users\c.ghanem\Downloads\UAB` as the working directory.

```powershell
# 1) Go to the folder
cd C:\Users\[...]

# 2) Build the image
docker build -t unifi-backup .

# 3) Run the container with a persistent appdata volume
docker run --name unifi-backup -p 5000:5000 `
  -e SECRET_KEY="REPLACE_WITH_A_STRONG_SECRET_KEY" `
  -e CHROME_HEADLESS="true" `
  -v ${PWD}\unifi_app:/app/unifi_app `
  unifi-backup
```

## Docker Login Workflow (Cookie Upload)
Docker containers cannot open a visible Chrome window. Use the **Manual Cookie Upload** section in the dashboard.

### How to export cookies (Chrome / Edge)
1. Open **https://unifi.ui.com** and sign in.
2. Install the **Export Cookie JSON File** extension:
   - https://chromewebstore.google.com/detail/export-cookie-json-file-f/nmckokihipjgplolmcmjakknndddifde?hl=en
3. Use the extension to export cookies for `unifi.ui.com` as JSON.
4. Save the JSON as a file (example: `cookies.json`).
5. In the dashboard, upload the JSON in **Manual Cookie Upload**.

### How to export cookies (Firefox)
1. Open **https://unifi.ui.com** and sign in.
2. Use an extension like **“Cookie Quick Manager”** to export cookies as JSON.
3. Upload the JSON in the dashboard.

> **Security Tip:** Only share cookies with trusted systems. Cookies act like a session token.

## Bulk Console Import
You can import multiple consoles from a JSON file that contains a top-level `consoles` list (and optional `master_logged_in`).
Use the **Bulk Console Import** section in the dashboard, or upload a JSON file similar to the example in the prompt.

If you make your own import file format it as such :

```bash
{
  "master_logged_in": false,
  "consoles": [
    {
      "id": 1,
      "name": "XXXXXXXXXX",
      "backup_url": "https://unifi.ui.com/consoles/9C[...]33/network/default/settings/system/backups",
    },
    {
      "id": 2,
      "name": "XXXXXXXXXX",
      "backup_url": "https://unifi.ui.com/consoles/D8B[...]03/network/default/settings/system/backups",
    },
    {
      "id": 3,
      "name": "XXXXXXXXXX",
      "backup_url": "https://unifi.ui.com/consoles/784[...]08/network/default/settings/system/backups",
    },
```


## SMTP Notifications
Enable SMTP in the dashboard to receive email notifications for:
- cookies expired
- backup failed
- backup success

Include SMTP host, port, credentials, sender, recipients (comma-separated), and SSL toggle.

## Docker Compose
```bash
docker compose up --build
```

## URLs

Make sure to use consoles urls formated as such :

```bash
https://unifi.ui.com/consoles/9C05D6[...]733/network/default/settings/system/backups
```

## Configuration
- `SECRET_KEY`: Flask secret key.
- `APP_DATA_DIR`: Data directory for logs, cookies, backups. Defaults to `./unifi_app`.
- `CHROME_HEADLESS`: `true`/`false` to run headless browser.
- `CHROME_BINARY`: Path to Chromium/Chrome binary (optional).
- `CHROMEDRIVER_PATH`: Path to chromedriver (Docker uses `/usr/bin/chromedriver`).
- `DEFAULT_TZ`: Default time zone used on first run. The dashboard now lists all available time zones.

## GUI :

![screencapture](https://github.com/user-attachments/assets/927ad023-6d8b-4926-95c1-6cfae2c453e1)


## Troubleshooting
- **Chrome/Chromedriver errors**: Ensure Chromium is installed in Docker or Chrome is installed locally.
- **Login expires**: Upload fresh cookies or re-run manual login locally to refresh.
