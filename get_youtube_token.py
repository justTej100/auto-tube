"""
Run this ONCE on your laptop (not in GitHub Actions) to get a YouTube
refresh token. It opens a browser for you to log in and consent, then
prints a refresh token you paste into GitHub Secrets as
YOUTUBE_REFRESH_TOKEN.

Prerequisites:
  1. In Google Cloud Console, create an OAuth 2.0 Client ID of type
     "Desktop app". Download the client_secret.json file into this folder.
  2. pip install google-auth-oauthlib

After this, you never need to run it again unless the token is revoked.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)

print("\n--- Save these as GitHub repo secrets ---")
print("YOUTUBE_CLIENT_ID:", creds.client_id)
print("YOUTUBE_CLIENT_SECRET:", creds.client_secret)
print("YOUTUBE_REFRESH_TOKEN:", creds.refresh_token)
