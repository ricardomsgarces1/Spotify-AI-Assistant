# utils/auth.py

import os
# ⚠️ Only for local/dev testing! Do NOT use in production.
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

import json
from dotenv import load_dotenv
from requests_oauthlib import OAuth2Session

load_dotenv()

CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI  = os.getenv("SPOTIFY_REDIRECT_URI")
TOKEN_URL     = "https://accounts.spotify.com/api/token"
AUTH_URL      = "https://accounts.spotify.com/authorize"
SCOPE         = ["user-read-playback-state", "user-modify-playback-state"]
TOKEN_FILE    = os.getenv("TOKEN_FILE", "spotify_token.json")

def save_token(token: dict):
    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f)

def load_token() -> dict:
    try:
        return json.load(open(TOKEN_FILE))
    except FileNotFoundError:
        return {}

def get_oauth_session():

    extra = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
    token = load_token()
    oauth = OAuth2Session(
        CLIENT_ID,
        token=token or None,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        auto_refresh_url=TOKEN_URL,
        auto_refresh_kwargs=extra,
        token_updater=save_token,
    )
    if not token:
        auth_url, state = oauth.authorization_url(AUTH_URL)
        print(f"Visit this URL to authorize (state={state}):\n{auth_url}")
        redirect_response = input("Paste the **full** callback URL here: ").strip()
        # now fetch_token will compare the session.state to the state in that URL
        token = oauth.fetch_token(
            TOKEN_URL,
            authorization_response=redirect_response,
            client_secret=CLIENT_SECRET,
        )
        save_token(token)
    return oauth
