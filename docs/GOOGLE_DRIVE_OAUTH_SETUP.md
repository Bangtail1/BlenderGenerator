# Google Drive OAuth 2.0 Setup Guide

Blender Asset Vault integrates directly with the official Google Drive API v3 using OAuth 2.0 and the least-privilege `https://www.googleapis.com/auth/drive.file` scope (accessing only folders and files created or selected by this application).

## 1. Create a Google Cloud Project & Enable Drive API

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g., `Blender-Asset-Vault`) or select an existing one.
3. Navigate to **APIs & Services -> Library**.
4. Search for **Google Drive API** and click **Enable**.

## 2. Configure the OAuth Consent Screen

1. Go to **APIs & Services -> OAuth consent screen**.
2. Choose **External** (or **Internal** for Google Workspace accounts).
3. Fill in the app name (`Blender Asset Vault`), support email, and developer contact email.
4. Under **Scopes**, add:
   - `https://www.googleapis.com/auth/drive.file`
5. Under **Test users** (while the app is in Testing mode), add your own Google account email address.

## 3. Create OAuth 2.0 Desktop Client Credentials

1. Go to **APIs & Services -> Credentials**.
2. Click **+ Create Credentials -> OAuth client ID**.
3. Select **Application type: Desktop app** (Desktop OAuth allows both local loopback redirect URIs for the Web UI and CLI `run_local_server`).
   - *Optional*: If authorizing via the Web UI callback on a fixed port, you may also add `http://127.0.0.1:8765/api/gdrive/oauth-callback` or use a **Web application** client with that exact authorized redirect URI.
4. Download the JSON file and save it locally (never commit it to Git!) at:

```text
./credentials/client_secrets.json
```

(Or set `GDRIVE_CLIENT_SECRETS_PATH` in `.env`.)

## 4. Secure Token Storage

- Tokens are stored at `GDRIVE_TOKEN_PATH` (default `./credentials/gdrive_token.json`).
- On Windows, refresh/access tokens are encrypted automatically using **Windows DPAPI** (`CryptProtectData`) scoped to your current OS user account.
- Cross-platform or explicit Fernet encryption can also be enabled by setting `VAULT_TOKEN_ENCRYPTION_KEY` to a generated Fernet key:

```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

- Both `.env` and `credentials/` are listed in `.gitignore`.

## 5. Connect & Select Destination Folder

Option A (Web UI):
1. Start the app (`blender-vault serve`) and open `http://127.0.0.1:8765`.
2. Click **Google Drive** in the top bar -> **Connect Google Drive (OAuth 2.0)**.
3. Complete consent in your browser, then choose or paste your destination folder ID and click **Set Folder & Initialize Vault Tree**.

Option B (CLI):
```powershell
.\.venv\Scripts\python.exe -m blender_vault.cli gdrive-auth
```

## Remote Folder Structure Created Automatically

```text
Blender Asset Vault/
├── Approved Assets/
│   ├── Architecture/
│   ├── Furniture/
│   ├── Characters/
│   ├── Mechanical Objects/
│   ├── Vehicles/
│   ├── Household Objects/
│   ├── Nature/
│   └── Miscellaneous/
├── Pending Review/
├── Rejected Assets/
├── Preview Images/
├── QA Reports/
└── Library Index/
```
