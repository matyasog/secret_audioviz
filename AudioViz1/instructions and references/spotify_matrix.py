#!/usr/bin/env python3
import time
import json
import os
import requests
from PIL import Image
from io import BytesIO
from rgbmatrix import RGBMatrix, RGBMatrixOptions

CLIENT_ID = ""
CLIENT_SECRET = ""
REDIRECT_URI = "http://localhost:8888/callback"

TOKEN_FILE = "/tmp/spotify_token.json"

def load_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return None

def save_token(token):
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f)

def request_token(code):
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    r = requests.post("https://accounts.spotify.com/api/token", data=data)
    return r.json()

def refresh_access_token(refresh_token):
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    r = requests.post("https://accounts.spotify.com/api/token", data=data)
    return r.json()

def get_auth_code():
    return input("Code: ").strip()

def get_token():
    token = load_token()
    if token and "refresh_token" in token:
        try:
            new_token = refresh_access_token(token["refresh_token"])
            if "refresh_token" not in new_token:
                new_token["refresh_token"] = token["refresh_token"]
            save_token(new_token)
            return new_token
        except:
            pass
    
    url = (
        "https://accounts.spotify.com/authorize"
        f"?client_id={CLIENT_ID}"
        "&response_type=code"
        f"&redirect_uri={REDIRECT_URI}"
        "&scope=user-read-currently-playing"
    )
    print("otevři odkaz, přihlas se, zkopíruj code, vlož code do terminálu:")
    print(url)
    code = get_auth_code()
    token = request_token(code)
    save_token(token)
    return token

options = RGBMatrixOptions()
options.rows = 64
options.cols = 64
options.chain_length = 1
options.parallel = 1
options.hardware_mapping = 'adafruit-hat'
options.brightness = 60
options.pwm_bits = 8
options.gpio_slowdown = 4

matrix = RGBMatrix(options=options)

while True:
    try:
        token = get_token()
        headers = {"Authorization": f"Bearer {token['access_token']}"}
        r = requests.get("https://api.spotify.com/v1/me/player/currently-playing", headers=headers)
        
        if r.status_code == 200 and r.json().get("item"):
            url = r.json()["item"]["album"]["images"][0]["url"]
            print("Stahuji cover z URL:", url)
            response = requests.get(url)
            img = Image.open(BytesIO(response.content))
            img = img.resize((64, 64))
        else:
            img = Image.new("RGB", (64, 64), color=(255, 0, 0))
    except Exception as e:
        print("Chyba:", e)
        img = Image.new("RGB", (64, 64), color=(255, 0, 0))
    
    matrix.SetImage(img.convert("RGB"))
    time.sleep(5)
