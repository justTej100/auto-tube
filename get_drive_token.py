"""
Run this ONCE on your laptop (not in GitHub Actions) to get a Google Drive
refresh token. Service accounts can't upload to a personal Drive account
(they have no storage quota of their own) — so uploads need to happen as
your actual Google account instead, via this refresh token.

Prerequisites:
  1. In Google Cloud Console, create an OAuth 2.0 Client ID of type
     "Desktop app" (same one you may have already made for YouTube, or a
     new one). Download it as client_secret.json into this folder.
  2. pip install google-auth-oauthlib

After this, you never need to run it again unless the token is revoked.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

# drive.file: the app can only see/manage files IT creates — it can't
# browse or read anything else in your Drive. Least-privilege scope for
# what this pipeline actually needs.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)

print("\n--- Save these as GitHub repo secrets ---")
print("GOOGLE_CLIENT_ID:", creds.client_id)
print("GOOGLE_CLIENT_SECRET:", creds.client_secret)
print("GOOGLE_REFRESH_TOKEN:", creds.refresh_token)
