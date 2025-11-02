#!/usr/bin/env python3
"""
jarvis_fine.py — single-file Jarvis for Windows 11 with pyttsx3 TTS,
Whisper STT integration (local model preferred), voice enrollment & verification,
and full OS/web control (apps, Wi-Fi, Bluetooth, battery, shutdown/restart/lock).

Patched for:
- English-only STT + initial_prompt command bias
- Fallback to English-only "small.en" if fine-tuned path fails
- VAD disabled by default (robust listening)
- Clean TTS shutdown (avoids pyttsx3 suppress error)
- Wake word anywhere + fuzzy + allow “open …” without wake
- SMART parsing for multi-command and follow-ups with short-term context
- Global media controls + YouTube shortcuts + scroll controls
- Volume control: set/increase/decrease by percentage (pycaw precise if available)
- Brightness control: set/increase/decrease by percentage (PowerShell WMI)
"""

import os
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import sys
import time
import json
import re
import subprocess
import logging
import logging.handlers
import wave
import tempfile
import atexit
import gc
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import quote_plus
from urllib.parse import urlencode
import webbrowser
import shutil
import threading
try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

try:
    import pyperclip
except Exception:
    pyperclip = None

# Optional packages
try:
    import numpy as np
except Exception:
    np = None

try:
    import sounddevice as sd
except Exception:
    sd = None

try:
    import whisper  # openai-whisper
except Exception:
    whisper = None

try:
    import pyttsx3
except Exception:
    pyttsx3 = None

try:
    import psutil
except Exception:
    psutil = None

# Optional precise volume control
try:
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    PYCAW_AVAILABLE = True
except Exception:
    PYCAW_AVAILABLE = False

# Windows input (keyboard/mouse) via ctypes
import ctypes
from ctypes import wintypes

# Ensure global exists before any references
WHISPER_MODEL = None

# -------------------------
# CONFIG
# -------------------------
CONFIG = {
    "whisper_model_path": r"C:\Users\Sidhant Jha\OneDrive\Desktop\jarvis\jarvis_dataset\whisper_finetuned",
    "trusted_folder": r"C:\Users\Sidhant Jha\OneDrive\Desktop\jarvis\trusted",

    # Wake word and behavior
    "require_wake_word": True,
    "allow_open_without_wake": True,          # allow "open <app>" without wake word
    "allow_controls_without_wake": True,      # allow media/scroll/volume/brightness without wake word
    "wake_word": "jarvis",
    "wake_word_anywhere": True,
    "wake_word_fuzzy": True,                  # accept minor missays ("jardvis")
    "wake_word_fuzzy_threshold": 0.80,

    # TTS voice
    "voice_rate": 190,
    "voice_name_contains": "Zira",

    # STT settings
    "stt": {
        "language": "en",
        "force_language": True,
        "fallback_model": "small.en",
        "chunk_seconds": 4,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "initial_prompt_enabled": True,
        "initial_prompt_text": (
            "Commands are short and in English.\n"
            "Examples:\n"
            "jarvis open instagram\n"
            "jarvis open youtube\n"
            "jarvis open youtube music\n"
            "jarvis open spotify\n"
            "jarvis open youtube and play <song>\n"
            "jarvis play <song> on youtube\n"
            "jarvis play <song> on spotify\n"
            "jarvis search <query> on google\n"
            "jarvis scroll down\n"
            "jarvis scroll up ten\n"
            "jarvis page down\n"
            "jarvis play\n"
            "jarvis pause\n"
            "jarvis resume\n"
            "jarvis next\n"
            "jarvis previous\n"
            "jarvis mute\n"
            "jarvis volume up\n"
            "jarvis set volume to fifty percent\n"
            "jarvis increase volume by ten\n"
            "jarvis set brightness to seventy percent\n"
            "jarvis decrease brightness by twenty\n"
            "jarvis seek forward ten seconds\n"
            "jarvis fullscreen\n"
            "jarvis exit fullscreen\n"
            "jarvis turn on wifi\n"
            "jarvis turn off wifi\n"
            "jarvis connect wifi <ssid>\n"
            "jarvis turn on bluetooth\n"
            "jarvis turn off bluetooth\n"
            "jarvis list bluetooth devices\n"
            "jarvis battery status\n"
            "jarvis shutdown computer\n"
            "jarvis restart computer\n"
            "jarvis lock computer\n"
        ),
        # VAD disabled by default (re-enable carefully)
        "vad_auto_calibrate": False,
        "vad_calibration_seconds": 1.0,
        "vad_rms_multiplier": 0.0,
        "vad_min_threshold": 0.0,
        "debug": True,
    },

    # Spotify API credentials
    "spotify_client_id": os.getenv("SPOTIFY_CLIENT_ID"),
    "spotify_client_secret": os.getenv("SPOTIFY_CLIENT_SECRET"),
    "spotify_redirect_uri": os.getenv("SPOTIFY_REDIRECT_URI"),

    # YouTube API key
    "youtube_api_key": os.getenv("YOUTUBE_API_KEY"),

    # app_paths may contain local executables, folders, files, ms-protocols, or HTTP URLs
    "app_paths": {
        # System / Utilities
        "notepad": r"C:\Windows\System32\notepad.exe",
        "calculator": "calc.exe",
        "settings": "ms-settings:",
        "paint": r"C:\Windows\System32\mspaint.exe",
        "wordpad": r"C:\Program Files\Windows NT\Accessories\wordpad.exe",
        "cmd": r"C:\Windows\System32\cmd.exe",
        "control_panel": r"C:\Windows\System32\control.exe",
        "file_explorer": r"C:\Windows\explorer.exe",
        "snipping_tool": r"C:\Windows\System32\SnippingTool.exe",
        "task_manager": r"C:\Windows\System32\Taskmgr.exe",
        "camera": r"C:\Windows\System32\microsoft.windows.camera.exe",
        "device_manager": r"C:\Windows\System32\devmgmt.msc",
        "services": r"C:\Windows\System32\services.msc",
        "registry_editor": r"C:\Windows\regedit.exe",
        "bluetooth_settings": "ms-settings:bluetooth",
        "network_settings": "ms-settings:network",
        "display_settings": "ms-settings:display",
        "update_settings": "ms-settings:windowsupdate",

        # Browsers
        "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "edge": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "firefox": r"C:\Program Files\Mozilla Firefox\firefox.exe",
        "brave": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",

        # Communication / Web Apps
        "instagram": "https://www.instagram.com",
        "facebook": "https://www.facebook.com",
        "whatsapp": "https://web.whatsapp.com",
        "telegram": "https://web.telegram.org",
        "discord": r"C:\Users\Sidhant Jha\AppData\Local\Discord\Update.exe --processStart Discord.exe",
        "zoom": r"C:\Users\Sidhant Jha\AppData\Roaming\Zoom\bin\Zoom.exe",
        "slack": r"C:\Users\Sidhant Jha\AppData\Local\slack\slack.exe",

        # Productivity
        "vscode": r"C:\Users\Sidhant Jha\AppData\Local\Programs\Microsoft VS Code\Code.exe",
        "word": r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
        "excel": r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE",
        "powerpoint": r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
        "onenote": r"C:\Program Files\Microsoft Office\root\Office16\ONENOTE.EXE",

        # Entertainment
        "spotify": r"C:\Users\Sidhant Jha\AppData\Roaming\Spotify\Spotify.exe",
        "youtube": "https://www.youtube.com",
        "youtube_music": "https://music.youtube.com",
        "netflix": "https://www.netflix.com",
        "prime_video": "https://www.primevideo.com",

        # Search Engines / General
        "google": "https://www.google.com",
        "bing": "https://www.bing.com",
        "chatgpt": "https://chat.openai.com",
    },

    "app_aliases": {
        # Browsers
        "google chrome": "chrome",
        "chrome browser": "chrome",
        "microsoft edge": "edge",
        "ms edge": "edge",
        "fire fox": "firefox",
        "brave browser": "brave",

        # System
        "command prompt": "cmd",
        "terminal": "cmd",
        "explorer": "file_explorer",
        "windows explorer": "file_explorer",
        "open control panel": "control_panel",
        "taskmanager": "task_manager",
        "camera app": "camera",
        "screenshot tool": "snipping_tool",
        "paint app": "paint",

        # Settings
        "bluetooth": "bluetooth_settings",
        "network": "network_settings",
        "display": "display_settings",
        "windows update": "update_settings",

        # Social / Communications
        "insta": "instagram",
        "ig": "instagram",
        "facebook app": "facebook",
        "fb": "facebook",
        "whatsapp web": "whatsapp",
        "telegram web": "telegram",
        "discord app": "discord",
        "zoom app": "zoom",
        "slack app": "slack",

        # Productivity
        "vs code": "vscode",
        "visual studio code": "vscode",
        "microsoft word": "word",
        "ms word": "word",
        "microsoft excel": "excel",
        "ms excel": "excel",
        "microsoft powerpoint": "powerpoint",
        "ms powerpoint": "powerpoint",
        "microsoft onenote": "onenote",
        "ms onenote": "onenote",

        # Entertainment
        "spotify app": "spotify",
        "music": "spotify",
        "yt": "youtube",
        "youtube app": "youtube",
        "youtube music app": "youtube_music",
        "yt music": "youtube_music",
        "netflix app": "netflix",
        "prime": "prime_video",
        "amazon prime": "prime_video",

        # Search / AI
        "google search": "google",
        "bing search": "bing",
        "open ai": "chatgpt",
        "chat gpt": "chatgpt",
        "gpt": "chatgpt",
    },

    # Voice profile & verification
    "voice_profile_file": "voice_profile.json",
    "enroll_phrases": [
        "the quick brown fox jumps over the lazy dog",
        "my voice is my secure password verify",
        "jarvis authenticate my identity now"
    ],
    "enroll_seconds": 4,
    "verify_seconds": 4,
    "voice_similarity_threshold": 0.55,
    "max_auth_attempts": 3,
    "auth_lockout_seconds": 300,
    "session_timeout_seconds": 1800,

    # Logging
    "log_file": "jarvis_audit.log",
    "log_max_bytes": 10 * 1024 * 1024,
    "log_backup_count": 5,
}
DEFAULT_SAMPLE_RATE = 16000

# -------------------------
# Logging setup
# -------------------------
logger = logging.getLogger("jarvis")
logger.setLevel(logging.DEBUG)
try:
    handler = logging.handlers.RotatingFileHandler(
        CONFIG["log_file"], maxBytes=CONFIG["log_max_bytes"], backupCount=CONFIG["log_backup_count"]
    )
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)
except Exception:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(console_handler)

def log_action(action: str, message: str = "", level: str = "INFO"):
    msg = f"{action} - {message}" if message else action
    level = (level or "INFO").upper()
    getattr(logger, level.lower(), logger.info)(msg)

# -------------------------
# TTS helpers (pyttsx3)
# -------------------------
tts_engine = None

def setup_tts():
    global tts_engine
    if pyttsx3 is None:
        print("pyttsx3 not available — TTS will fall back to prints.")
        return None
    try:
        driver_name = "sapi5" if os.name == "nt" else None
        tts_engine = pyttsx3.init(driverName=driver_name) if driver_name else pyttsx3.init()
        tts_engine.setProperty("rate", CONFIG.get("voice_rate", 190))
        wanted = CONFIG.get("voice_name_contains", "").lower()
        try:
            voices = tts_engine.getProperty("voices")
            for v in voices:
                if wanted and wanted in (v.name or "").lower():
                    tts_engine.setProperty("voice", v.id)
                    break
        except Exception:
            pass
        log_action("tts_initialized")
        return tts_engine
    except Exception as e:
        log_action("tts_init_error", str(e), "ERROR")
        return None

def say(text: str):
    try:
        print(f"[JARVIS]: {text}")
        if tts_engine:
            tts_engine.say(text)
            tts_engine.runAndWait()
    except Exception as e:
        print("[TTS ERROR]:", e)

def shutdown_tts():
    global tts_engine
    try:
        if tts_engine is not None:
            try:
                tts_engine.stop()
            except Exception:
                pass
            tmp = tts_engine
            tts_engine = None
            del tmp
            gc.collect()
    except Exception:
        pass

atexit.register(shutdown_tts)

# -------------------------
# Utility helpers
# -------------------------
def safe_input(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return ""
    except Exception:
        return ""

def is_admin() -> bool:
    try:
        if os.name == 'nt':
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            return os.geteuid() == 0
    except Exception:
        return False

def is_running_from_trusted_folder():
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        return False
    cfg_trusted = CONFIG.get("trusted_folder") or ""
    if not cfg_trusted:
        return True
    def norm(p):
        p = os.path.abspath(p)
        p = os.path.normpath(p)
        if os.name == "nt":
            p = os.path.normcase(p)
        return p
    return norm(script_dir) == norm(cfg_trusted)

# -------------------------
# Shell helpers
# -------------------------
def run_cmd(cmd_list, capture_output=True, shell=False):
    try:
        result = subprocess.run(
            cmd_list,
            capture_output=capture_output,
            text=True,
            shell=shell
        )
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()
    except Exception as e:
        return 1, "", str(e)

def run_ps(script_or_code: str):
    if os.name != 'nt':
        return 1, "", "PowerShell only supported on Windows"
    if os.path.exists(script_or_code):
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", script_or_code]
    else:
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-Command", script_or_code]
    return run_cmd(cmd, shell=False)

# -------------------------
# Windows input helpers (keyboard/mouse)
# -------------------------
if os.name == 'nt':
    if os.name == 'nt':
    	# Return/arg types for stability
    	user32.GetForegroundWindow.restype = wintypes.HWND
    	user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    	user32.ShowWindowAsync.restype = ctypes.c_bool
    	user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    	user32.SetForegroundWindow.restype = ctypes.c_bool
    	user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    	user32.GetWindowRect.restype = ctypes.c_bool
    	user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    	user32.GetWindowTextLengthW.restype = ctypes.c_int
    	user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    	user32.GetWindowTextW.restype = ctypes.c_int

    # Mouse wheel
    MOUSEEVENTF_WHEEL = 0x0800
    WHEEL_DELTA = 120

    # Key flags
    KEYEVENTF_KEYUP = 0x0002

    # VK codes
    VK_SPACE = 0x20
    VK_LEFT = 0x25
    VK_UP = 0x26
    VK_RIGHT = 0x27
    VK_DOWN = 0x28
    VK_PRIOR = 0x21  # Page Up
    VK_NEXT = 0x22   # Page Down
    VK_HOME = 0x24
    VK_END = 0x23
    VK_CONTROL = 0x11
    VK_SHIFT = 0x10
    VK_ALT = 0x12

    # Media keys
    VK_MEDIA_NEXT_TRACK = 0xB0
    VK_MEDIA_PREV_TRACK = 0xB1
    VK_MEDIA_STOP = 0xB2
    VK_MEDIA_PLAY_PAUSE = 0xB3
    VK_VOLUME_MUTE = 0xAD
    VK_VOLUME_DOWN = 0xAE
    VK_VOLUME_UP = 0xAF

    if os.name == 'nt':
    VK_UP = 0x26
    VK_DOWN = 0x28

    def maximize_active_window():
        try:
            # Safer: Win+Up twice to maximize
            press_combo(VK_LWIN, VK_UP)
            time.sleep(0.05)
            press_combo(VK_LWIN, VK_UP)
            say("Maximized window.")
            log_action("win_maximize_active", "")
            return True
        except Exception as e:
            log_action("win_maximize_active_error", str(e), "ERROR")
            # Fallback via ShowWindowAsync
            try:
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    ShowWindowAsync(hwnd, 3)  # SW_MAXIMIZE
                    say("Maximized window.")
                    return True
            except Exception as e2:
                log_action("win_maximize_fallback_error", str(e2), "ERROR")
            say("I couldn't maximize the active window.")
            return False

    def restore_active_window():
        try:
            # Win+Down restores (twice if maximized)
            press_combo(VK_LWIN, VK_DOWN)
            time.sleep(0.05)
            press_combo(VK_LWIN, VK_DOWN)
            say("Restored window.")
            log_action("win_restore_active", "")
            return True
        except Exception as e:
            log_action("win_restore_active_error", str(e), "ERROR")
            # Fallback via ShowWindowAsync
            try:
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    ShowWindowAsync(hwnd, 9)  # SW_RESTORE
                    say("Restored window.")
                    return True
            except Exception as e2:
                log_action("win_restore_fallback_error", str(e2), "ERROR")
            say("I couldn't restore the active window.")
            return False

    def press_vk(vk):
        try:
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.01)
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        except Exception as e:
            log_action("press_vk_error", f"vk={vk} {e}", "ERROR")

    def press_char(c):
        vk = ord(c.upper()) if isinstance(c, str) and len(c) == 1 else c
        press_vk(vk)

    def press_combo(*vks):
        try:
            for vk in vks:
                user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.02)
            for vk in reversed(vks):
                user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        except Exception as e:
            log_action("press_combo_error", str(e), "ERROR")

    def mouse_scroll(notches: int):
        try:
            user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(notches) * WHEEL_DELTA, 0)
        except Exception as e:
            log_action("mouse_scroll_error", str(e), "ERROR")
else:
    def press_vk(vk): pass
    def press_char(c): pass
    def press_combo(*vks): pass
    def mouse_scroll(notches: int): pass

# -------------------------
# App / Open helpers
# -------------------------
def verify_app_path(path: str) -> bool:
    if not path:
        return False
    p = str(path)
    if p.startswith(("http://", "https://", "ms-settings:", "ms-")):
        return True
    base = p.split()[0].strip('"')
    if os.path.isabs(base):
        return os.path.exists(base)
    return shutil.which(base) is not None

def open_app(name: str):
    if not name:
        say("No application specified.")
        return
    name_lower = name.strip().lower()
    name_safe = CONFIG.get("app_aliases", {}).get(name_lower, name_lower)

    app_paths = CONFIG.get("app_paths", {})
    if name_safe not in app_paths:
        say(f"{name} is not in the approved application list.")
        log_action("open_app_blocked", name, "WARNING")
        return

    target = app_paths[name_safe]

    if target.startswith(("http://", "https://")):
        try:
            webbrowser.open(target)
            say(f"Opening {name}.")
            log_action("open_web", name)
        except Exception as e:
            say(f"I couldn't open {name}.")
            log_action("open_web_error", f"{name}: {str(e)}", "ERROR")
        return

    if os.name == 'nt' and target.startswith(("ms-settings:", "ms-")):
        try:
            os.startfile(target)
            say(f"Opening {name}.")
            log_action("open_uri", name)
        except Exception as e:
            say(f"I couldn't open {name}.")
            log_action("open_uri_error", f"{name}: {str(e)}", "ERROR")
        return

    try:
        if not verify_app_path(target):
            say(f"The path for {name} does not exist or is invalid.")
            log_action("open_app_path_invalid", f"{name}: {target}", "ERROR")
            return
        subprocess.Popen(target, shell=True)
        say(f"Opening {name}.")
        log_action("open_app", name)
    except Exception as e:
        say(f"I couldn't open {name}.")
        log_action("open_app_error", f"{name}: {str(e)}", "ERROR")

def close_app(name: str):
    """Close local apps (taskkill) or close current tab for web apps."""
    key = CONFIG["app_aliases"].get(name.strip().lower(), name.strip().lower())
    app_paths = CONFIG["app_paths"]
    
    if key not in app_paths:
        say(f"I don't recognize {name} in the approved list.")
        return

    target = app_paths[key]

    # Web/URI: close current tab/window
    if target.startswith(("http://", "https://", "ms-settings:", "ms-")):
        press_combo(VK_CONTROL, ord('W'))  # Ctrl+W
        say(f"Closed {key.replace('_',' ')} tab.")
        return

    # Local exe: kill by process name
    base = os.path.basename(target.split()[0])
    rc, out, err = run_cmd(["taskkill", "/IM", base, "/F"])
    
    if rc == 0:
        say(f"Closed {key.replace('_',' ')}.")
        log_action("close_app_kill", f"{key} -> {base}")
    else:
        # Fallback: Alt+F4 on active window
        press_combo(VK_ALT, 0x73)  # 0x73 = F4
        say(f"Tried to close {key.replace('_',' ')}.")
        log_action("close_app_altf4", f"{key}: rc={rc}, {err or out}")


# -------------------------
# Context + intents (play/search/follow-ups)
# -------------------------
LAST_CONTEXT = {"app_key": None, "ts": 0.0}
FOLLOWUP_WINDOW_SEC = 20.0

def set_context(app_key: str):
    LAST_CONTEXT["app_key"] = app_key
    LAST_CONTEXT["ts"] = time.time()

def get_recent_context():
    if LAST_CONTEXT["app_key"] and (time.time() - LAST_CONTEXT["ts"] <= FOLLOWUP_WINDOW_SEC):
        return LAST_CONTEXT["app_key"]
    return None

# -------------------------
# YouTube results cache + ordinal selection (Step 1, brace-safe)
# -------------------------
YT_RESULTS = {"query": None, "items": [], "ts": 0.0}

def set_youtube_results(query: str, items: list):
    """
    Cache the most recent YouTube search results so follow-ups like
    'play second video' can select by index without re-searching.
    """
    try:
        global YT_RESULTS
        YT_RESULTS = {
            "query": (query or "").strip(),
            "items": items or [],
            "ts": time.time(),
        }
        set_context("youtube")
        log_action("youtube_results_cached", "q='{}' n={}".format(YT_RESULTS['query'], len(items or [])))
    except Exception as e:
        log_action("youtube_results_cache_error", str(e), "ERROR")

def get_youtube_results(max_age: float = FOLLOWUP_WINDOW_SEC):
    """
    Return cached results if still fresh (<= max_age seconds), else None.
    Structure: {"query": str, "items": [..], "ts": float}
    """
    try:
        if not (YT_RESULTS.get("items") or []):
            return None
        if (time.time() - float(YT_RESULTS.get("ts") or 0.0)) <= float(max_age):
            return YT_RESULTS
        return None
    except Exception as e:
        log_action("youtube_results_get_error", str(e), "ERROR")
        return None

# Word ordinals
ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10
}

def ordinal_suffix(n: int) -> str:
    n = int(n)
    if 10 <= (n % 100) <= 20:
        return "th"
    last = n % 10
    if last == 1:
        return "st"
    if last == 2:
        return "nd"
    if last == 3:
        return "rd"
    return "th"

def ordinal_word(n: int) -> str:
    """
    Nicely spoken ordinal for TTS feedback (first, second... 11th, 21st, etc.).
    """
    n = int(n)
    base = {1:"first",2:"second",3:"third",4:"fourth",5:"fifth",
            6:"sixth",7:"seventh",8:"eighth",9:"ninth",10:"tenth"}
    if n in base:
        return base[n]
    return "{}{}".format(n, ordinal_suffix(n))

def extract_ordinal_index(text: str):
    """
    Pull an index from phrases like 'second', '3rd', '4th', '2', etc.
    Returns int or None.
    """
    t = (text or "").lower()

    # word ordinals
    for w, n in ORDINAL_WORDS.items():
        pattern = r"\b" + re.escape(w) + r"\b"
        if re.search(pattern, t):
            return n

    # numeric ordinals/cardinals
    m = re.search(r'\b(\d+)\s*(?:st|nd|rd|th)?\b', t)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    return None

def parse_nth_play_command(text: str):
    """
    Detect selections like:
      - 'play second video'
      - 'open 3rd result'
      - 'and play 4th one'
      - 'play 2nd on youtube'
    Returns (index:int, target:str|None) or None.
    target is 'youtube' or 'youtube_music' if explicitly mentioned.
    """
    t = (text or "").lower().strip()
    # remove filler
    t = re.sub(r'^(and|then|please)\s+', '', t)
    # must be a play/open command
    if not re.search(r'\b(play|open)\b', t):
        return None
    # must look like selecting an item
    looks_like_selection = (
        re.search(r'\b(video|result|one|option)\b', t) or
        re.search(r'\b(first|second|third|\d+(?:st|nd|rd|th)?)\b', t)
    )
    if not looks_like_selection:
        return None

    idx = extract_ordinal_index(t)
    if not idx:
        return None

    # optional explicit target
    target = None
    m_app = re.search(r'\b(on|in)\s+(youtube music|youtube)\b', t)
    if m_app:
        target = "youtube_music" if "music" in m_app.group(2) else "youtube"
    return (idx, target)

def canonical_app_key(name: str):
    n = name.strip().lower()
    return CONFIG["app_aliases"].get(n, n) if n else None

def resolve_app_from_phrase(phrase: str):
    phrase_l = phrase.lower()
    aliases = list(CONFIG["app_aliases"].keys())
    keys = list(CONFIG["app_paths"].keys())
    candidates = list(set(aliases + keys))
    candidates.sort(key=lambda s: len(s), reverse=True)

    for cand in candidates:
        pattern = r'\b' + re.escape(cand.lower()) + r'\b'
        if re.search(pattern, phrase_l):
            canon = CONFIG["app_aliases"].get(cand.lower(), cand.lower())
            if canon in CONFIG["app_paths"]:
                return canon, cand
    for cand in candidates:
        if cand.lower() in phrase_l:
            canon = CONFIG["app_aliases"].get(cand.lower(), cand.lower())
            if canon in CONFIG["app_paths"]:
                return canon, cand
    return None, None

def clean_query(q: str) -> str:
    if not q:
        return ""
    q = q.strip()
    q = re.sub(r'^(and|then|please)\b', '', q, flags=re.I).strip()
    q = re.sub(r'[.。…]+$', '', q).strip()
    return q

def extract_query_after_app(phrase: str, matched_alias: str):
    if not matched_alias:
        return ""
    phrase_l = phrase.lower()
    idx = phrase_l.find(matched_alias.lower())
    tail = phrase[idx + len(matched_alias):] if idx >= 0 else phrase
    tail = clean_query(tail)
    m = re.search(r'\bplay\b\s+(.+)$', tail, flags=re.I)
    if m:
        return clean_query(m.group(1))
    m = re.search(r'\bsearch(?:\s+for)?\b\s+(.+)$', tail, flags=re.I)
    if m:
        return clean_query(m.group(1))
    return clean_query(tail)

# ------------- Spotify robustness helpers -------------
def _launch_spotify_app():
    try:
        open_app("spotify")  # uses your approved app list
        time.sleep(1.5)
    except Exception as e:
        log_action("spotify_launch_error", str(e), "ERROR")

def _open_spotify_uri_or_web(uri: str, track_id: str):
    # Try to open the track in the desktop app via URI; fallback to web.
    try:
        if os.name == 'nt':
            os.startfile(uri)  # opens spotify:track:... in app
        else:
            webbrowser.open(uri)
        say("Opened the track in Spotify.")
        set_context("spotify")
        return True
    except Exception as e:
        log_action("spotify_uri_open_error", str(e), "WARNING")
        try:
            web_url = f"https://open.spotify.com/track/{track_id}"
            webbrowser.open(web_url)
            say("Opened the track on Spotify.")
            set_context("spotify")
            return True
        except Exception as e2:
            log_action("spotify_web_open_error", str(e2), "ERROR")
            return False
# -------------------------
# Spotify helpers (Spotipy)
# -------------------------

class SpotifyController:
    """
    Handles Spotify playback via Web API when possible (Premium + active device),
    and falls back to opening the track in the desktop app/web when not.
    """
    def __init__(self, client_id, client_secret, redirect_uri):
        self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope="user-read-playback-state,user-modify-playback-state,user-read-currently-playing",
            cache_path="spotify_token.json"
        ))

    def _wait_for_device(self, timeout=12.0, poll=0.8):
        """
        Wait for a Spotify device to appear. Launches app once if needed.
        Returns device_id or None.
        """
        launched = False
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                devices = self.sp.devices().get("devices", [])
            except Exception as e:
                log_action("spotify_devices_error", str(e), "ERROR")
                devices = []
            if devices:
                active = next((d for d in devices if d.get("is_active")), None)
                chosen = active or devices[0]
                dev_id = chosen.get("id")
                if dev_id:
                    try:
                        self.sp.transfer_playback(dev_id, force_play=True)
                    except Exception:
                        pass
                    return dev_id
            if not launched:
                _launch_spotify_app()
                launched = True
            time.sleep(poll)
        return None

    def _ensure_device(self):
        return self._wait_for_device(timeout=12.0, poll=0.8)

    def _play_on_device(self, uri: str, device_id: str) -> bool:
        try:
            self.sp.start_playback(uris=[uri], device_id=device_id)
            return True
        except Exception as e:
            log_action("spotify_start_playback_error", str(e), "ERROR")
            return False

    def play_query(self, query: str) -> bool:
        """
        Search the top track for 'query' and play it.
        - If API playback is allowed (Premium + active device), plays via API.
        - Otherwise opens the track in the desktop app (spotify: URI) or web as fallback.
        """
        try:
            res = self.sp.search(q=query, type="track", limit=1)
            items = res.get("tracks", {}).get("items", [])
            if not items:
                say(f"I couldn't find {query} on Spotify.")
                return False

            track = items[0]
            uri = track["uri"]
            track_id = track.get("id") or ""
            title = track.get("name", "track")
            artist = (track.get("artists") or [{}])[0].get("name", "")

            dev = self._ensure_device()
            if dev and self._play_on_device(uri, dev):
                say(f"Playing {title}{' by ' + artist if artist else ''} on Spotify.")
                set_context("spotify")
                return True

            # No API playback (no device / free account / error) -> open URI/web
            return _open_spotify_uri_or_web(uri, track_id)

        except Exception as e:
            log_action("spotify_play_query_error", f"{query}: {e}", "ERROR")
            return False

    def pause(self):
        try:
            self.sp.pause_playback()
            say("Playback paused on Spotify.")
            return True
        except Exception as e:
            log_action("spotify_pause_error", str(e), "ERROR")
            return False

    def resume(self):
        try:
            dev = self._ensure_device()
            if dev:
                self.sp.start_playback(device_id=dev)
            else:
                _launch_spotify_app()
                # If we cannot control it, at least bring the app up
            say("Playback resumed on Spotify.")
            return True
        except Exception as e:
            log_action("spotify_resume_error", str(e), "ERROR")
            return False

    def next(self):
        try:
            self.sp.next_track()
            say("Skipped to next track on Spotify.")
            return True
        except Exception as e:
            log_action("spotify_next_error", str(e), "ERROR")
            return False

    def previous(self):
        try:
            self.sp.previous_track()
            say("Went back to previous track on Spotify.")
            return True
        except Exception as e:
            log_action("spotify_prev_error", str(e), "ERROR")
            return False


# --- Spotify helpers (outside the class) ---

def _launch_spotify_app():
    """Open the Spotify desktop app (approved via CONFIG['app_paths'])."""
    try:
        open_app("spotify")
        time.sleep(1.5)
    except Exception as e:
        log_action("spotify_launch_error", str(e), "ERROR")

def _open_spotify_uri_or_web(uri: str, track_id: str):
    """
    Open a track directly in the desktop app via spotify: URI; fallback to web.
    Keeps context 'spotify' so follow-ups work.
    """
    try:
        if os.name == 'nt':
            os.startfile(uri)  # opens spotify:track:... in desktop app
        else:
            webbrowser.open(uri)
        say("Opened the track in Spotify.")
        set_context("spotify")
        return True
    except Exception as e:
        log_action("spotify_uri_open_error", str(e), "WARNING")
        try:
            webbrowser.open(f"https://open.spotify.com/track/{track_id}")
            say("Opened the track on Spotify.")
            set_context("spotify")
            return True
        except Exception as e2:
            log_action("spotify_web_open_error", str(e2), "ERROR")
            return False

_SPOTIFY = None  # singleton cache

def get_spotify():
    """
    Returns a cached SpotifyController, or None if spotipy/creds are missing.
    Uses CONFIG values or env vars (SPOTIFY_CLIENT_ID/SECRET/REDIRECT_URI).
    """
    global _SPOTIFY
    try:
        if _SPOTIFY is not None:
            return _SPOTIFY
        if spotipy is None:
            log_action("spotify_not_installed", "spotipy missing", "ERROR")
            return None
        cid = CONFIG.get("spotify_client_id") or os.getenv("SPOTIFY_CLIENT_ID")
        sec = CONFIG.get("spotify_client_secret") or os.getenv("SPOTIFY_CLIENT_SECRET")
        redir = CONFIG.get("spotify_redirect_uri") or os.getenv("SPOTIFY_REDIRECT_URI")
        if not (cid and sec and redir):
            log_action("spotify_missing_creds", "Set SPOTIFY_CLIENT_ID/SECRET/REDIRECT_URI", "ERROR")
            return None
        _SPOTIFY = SpotifyController(cid, sec, redir)
        return _SPOTIFY
    except Exception as e:
        log_action("get_spotify_error", str(e), "ERROR")
        return None

def _spotify_play_safe(query: str) -> bool:
    """
    Safely play a query:
    - If API control is available (Premium + device), uses SpotifyController.play_query
    - Else returns False so caller can fallback to web search
    """
    try:
        sp = get_spotify()
        if sp and hasattr(sp, "play_query"):
            return bool(sp.play_query(query))
        return False
    except Exception as e:
        log_action("spotify_play_query_call_error", str(e), "ERROR")
        return False# -------------------------
# YouTube helpers (consolidated)
# -------------------------
def _youtube_api_key():
    return (CONFIG.get("youtube_api_key") or os.getenv("YOUTUBE_API_KEY") or None)

def _find_yt_dlp():
    candidates = ["yt-dlp.exe", "yt-dlp", "yt_dlp.exe", "yt_dlp"]
    for name in candidates:
        local = os.path.join(os.getcwd(), name)
        if os.path.isfile(local):
            return local
    for name in candidates:
        p = shutil.which(name)
        if p:
            return p
    return None

def open_url_with_specific_browser(app_key: str, url: str) -> bool:
    """
    Launch a specific browser executable from CONFIG to open url, so the new
    window gains focus (helps keystrokes like 'K' reach YouTube).
    """
    app_paths = CONFIG.get("app_paths", {})
    target = app_paths.get(app_key)
    if not target or target.startswith(("http://", "https://", "ms-settings:", "ms-")):
        return False
    try:
        subprocess.Popen(f'"{target}" --new-window "{url}"', shell=True)
        return True
    except Exception as e:
        log_action("open_url_specific_browser_error", f"{app_key}: {e}", "ERROR")
        return False

def youtube_search_results_ytdlp(query: str, limit: int = 10):
    bin_path = _find_yt_dlp()
    if not bin_path:
        return None
    try:
        cmd = [bin_path, "-J", f"ytsearch{max(1, int(limit))}:{query}", "--skip-download", "--flat-playlist", "--no-warnings"]
        rc, out, err = run_cmd(cmd, capture_output=True, shell=False)
        if rc != 0 or not out:
            log_action("yt_dlp_error", err or out, "ERROR")
            return None
        data = json.loads(out)
        entries = data.get("entries") or []
        items = []
        for e in entries:
            vid = e.get("id") or e.get("url")
            if not vid:
                continue
            items.append({
                "videoId": vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": e.get("title") or "",
                "channel": e.get("uploader") or e.get("channel") or "",
            })
        return items
    except Exception as ex:
        log_action("yt_dlp_parse_error", f"{query}: {ex}", "ERROR")
        return None

def youtube_search_results_api(query: str, limit: int = 10, api_key: str = None):
    api_key = api_key or _youtube_api_key()
    if not api_key:
        return None
    try:
        maxResults = max(1, min(int(limit), 50))
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": maxResults,
            "safeSearch": "none",
            "key": api_key,
        }
        url = "https://www.googleapis.com/youtube/v3/search?" + urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        items = []
        for it in data.get("items") or []:
            vid = ((it.get("id") or {}).get("videoId")) or None
            if not vid:
                continue
            snip = it.get("snippet") or {}
            items.append({
                "videoId": vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": snip.get("title") or "",
                "channel": snip.get("channelTitle") or "",
            })
        return items
    except Exception as e:
        log_action("youtube_api_error", f"{query}: {e}", "ERROR")
        return None

def youtube_search_results(query: str, limit: int = 10, prefer_free: bool = True):
    items = None
    if prefer_free:
        items = youtube_search_results_ytdlp(query, limit=limit) or youtube_search_results_api(query, limit=limit)
    else:
        items = youtube_search_results_api(query, limit=limit) or youtube_search_results_ytdlp(query, limit=limit)
    return items or []

def youtube_play_query(query: str, prefer_music: bool = False, prefer_free: bool = True, index: int = 1) -> bool:
    q = (query or "").strip()
    if not q:
        return False

    if prefer_music:
        url = f"https://music.youtube.com/search?q={quote_plus(q)}"
        try:
            if not open_url_with_specific_browser("chrome", url):
                webbrowser.open(url)
            say(f"Searching {q} on YouTube Music.")
            set_context("youtube_music")
            log_action("youtube_music_search_open", q)
            return True
        except Exception as e:
            log_action("youtube_music_open_error", f"{q}: {e}", "ERROR")
            return False

    want = max(5, int(index))
    items = youtube_search_results(q, limit=want, prefer_free=prefer_free)
    if items:
        set_youtube_results(q, items)
        idx0 = max(1, int(index)) - 1
        info = items[idx0] if idx0 < len(items) else items[0]
        try:
            play_url = info["url"]
            sep = "&" if "?" in play_url else "?"
            play_url = f"{play_url}{sep}autoplay=1"

            if not open_url_with_specific_browser("chrome", play_url):
                webbrowser.open(play_url)

            for i in range(3):
                time.sleep(1.0 if i == 0 else 0.5)
                press_char('K')

            pos = idx0 + 1
            say(f"Playing {ordinal_word(pos)} result: {info.get('title') or q} on YouTube.")
            set_context("youtube")
            log_action("youtube_play_query", f"{q} -> {info['url']} (#{pos})")
            return True
        except Exception as e:
            log_action("youtube_open_error", f"{q}: {e}", "ERROR")

    try:
        url = f"https://www.youtube.com/results?search_query={quote_plus(q)}"
        if not open_url_with_specific_browser("chrome", url):
            webbrowser.open(url)
        try:
            items = youtube_search_results(q, limit=10, prefer_free=True)
            if items:
                set_youtube_results(q, items)
        except Exception as e:
            log_action("youtube_prime_cache_error", str(e), "WARNING")
        say(f"Searching {q} on YouTube.")
        set_context("youtube")
        log_action("youtube_search_fallback", q)
        return True
    except Exception as e:
        log_action("youtube_search_open_error", f"{q}: {e}", "ERROR")
        return False
# -------------------------
# Instagram helpers (Playwright UI automation)
# -------------------------
IG_PROFILE_DIR = os.path.join(os.getcwd(), "ig_profile")
IG = {
    "pw": None,
    "ctx": None,
    "page": None,
    "opening": False,  # NEW: mark when we are opening
    "auto": {"running": False, "thread": None, "interval": 2.0, "mode": "feed"}  # mode: 'feed'|'reels'
}


def ig_log(msg: str):
    log_action("instagram", msg)

def ig_cleanup():
    try:
        ig_auto_scroll_stop()
        if IG["page"]:
            try:
                IG["page"].close()
            except Exception:
                pass
            IG["page"] = None
        if IG["ctx"]:
            try:
                IG["ctx"].close()
            except Exception:
                pass
            IG["ctx"] = None
        if IG["pw"]:
            try:
                IG["pw"].stop()
            except Exception:
                pass
            IG["pw"] = None
    except Exception:
        pass

atexit.register(ig_cleanup)

def ig_ensure_open(reels: bool = False):
    """
    Ensure a persistent browser context and a page are open on Instagram.
    Use lighter waits so we don't block the voice loop forever.
    """
    if sync_playwright is None:
        # Don't TTS from here (this may run in a background thread)
        log_action("instagram", "playwright_missing")
        open_app("instagram")
        set_context("instagram")
        return None

    # If already opening in another thread, return whatever we have
    if IG.get("opening", False):
        return IG.get("page")

    IG["opening"] = True
    try:
        if IG["pw"] is None:
            IG["pw"] = sync_playwright().start()

        if IG["ctx"] is None:
            IG["ctx"] = IG["pw"].chromium.launch_persistent_context(
                user_data_dir=IG_PROFILE_DIR,
                headless=False,
                args=["--disable-notifications"]
            )

        if IG["page"] is None or IG["page"].is_closed():
            IG["page"] = IG["ctx"].new_page()

        url = "https://www.instagram.com/reels/" if reels else "https://www.instagram.com/"
        # Use domcontentloaded and short timeout to prevent blocking
        try:
            IG["page"].goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            # Navigation may time out due to long-polling; that's fine, keep the page
            log_action("instagram", f"goto_timeout_or_error: {e}")

        set_context("instagram")
        IG["auto"]["mode"] = "reels" if reels else "feed"
        return IG["page"]
    except Exception as e:
        ig_log(f"open_error: {e}")
        open_app("instagram")
        set_context("instagram")
        return None
    finally:
        IG["opening"] = False

def ig_open_feed():
    # Open in default browser
    say("Opening Instagram...")
    try:
        webbrowser.open("https://www.instagram.com/")
        set_context("instagram")
        log_action("instagram_open_default", "feed")
    except Exception as e:
        log_action("instagram_open_default_error", str(e), "ERROR")

def ig_open_reels():
    say("Opening Instagram Reels...")
    try:
        webbrowser.open("https://www.instagram.com/reels/")
        set_context("instagram")
        IG["auto"]["mode"] = "reels"
        log_action("instagram_open_default", "reels")
    except Exception as e:
        log_action("instagram_open_default_error", str(e), "ERROR")

def _ig_click_like(page, want_like=True):
    # Try accessible button first
    target_label = "Like" if want_like else "Unlike"
    try:
        btn = page.get_by_role("button", name=target_label)
        if btn.count() > 0:
            btn.first.click(timeout=2000)
            return True
    except Exception:
        pass
    # Try aria-label on svg or button
    try:
        el = page.locator(f'[aria-label="{target_label}"]').first
        if el.count() > 0:
            el.click(timeout=2000)
            return True
    except Exception:
        pass
    return False

def ig_like():
    p = ig_ensure_open()
    if not p:
        return False
    if _ig_click_like(p, want_like=True):
        say("Liked.")
        ig_log("like")
        return True
    # Double-click center as a fallback (often likes the post/reel)
    try:
        box = p.viewport_size or {"width": 1200, "height": 800}
        p.mouse.dblclick(box["width"]//2, int(box["height"]*0.35))
        say("Liked.")
        ig_log("like_dblclick")
        return True
    except Exception as e:
        ig_log(f"like_error: {e}")
        say("I couldn’t like it.")
        return False

def ig_unlike():
    p = ig_ensure_open()
    if not p:
        return False
    if _ig_click_like(p, want_like=False):
        say("Unliked.")
        ig_log("unlike")
        return True
    say("I couldn’t unlike it.")
    ig_log("unlike_error")
    return False

def ig_comment(text: str):
    p = ig_ensure_open()
    if not p:
        return False
    t = (text or "").strip()
    if not t:
        say("What should I comment?")
        return False
    # Comment box typically has aria-label like "Add a comment…"
    try:
        box = p.locator('textarea[aria-label*="comment"]').first
        if box.count() == 0:
            box = p.locator('textarea[placeholder*="comment"]').first
        if box.count() == 0:
            # Sometimes comments are collapsed; try pressing 'Enter' in the comment box toggler if visible
            toggler = p.get_by_text("Add a comment").first
            if toggler and toggler.count() > 0:
                toggler.click(timeout=2000)
                box = p.locator('textarea[aria-label*="comment"]').first
        if box.count() > 0:
            box.click(timeout=2000)
            box.fill(t)
            p.keyboard.press("Enter")
            say("Comment posted.")
            ig_log("comment_posted")
            return True
    except Exception as e:
        ig_log(f"comment_error: {e}")
    say("I couldn’t comment.")
    return False

def ig_next():
    p = ig_ensure_open()
    if not p:
        return False
    try:
        # Reels: PageDown or ArrowDown usually goes to the next reel
        if IG["auto"]["mode"] == "reels":
            p.keyboard.press("PageDown")
        else:
            p.keyboard.press("PageDown")
        say("Next.")
        ig_log("next")
        return True
    except Exception as e:
        ig_log(f"next_error: {e}")
        say("I couldn’t go next.")
        return False

def ig_prev():
    p = ig_ensure_open()
    if not p:
        return False
    try:
        if IG["auto"]["mode"] == "reels":
            p.keyboard.press("PageUp")
        else:
            p.keyboard.press("PageUp")
        say("Previous.")
        ig_log("prev")
        return True
    except Exception as e:
        ig_log(f"prev_error: {e}")
        say("I couldn’t go back.")
        return False

def ig_copy_link():
    p = ig_ensure_open()
    if not p:
        return False
    try:
        href = p.url
        # Try to get a permalink if viewing inside a modal
        try:
            a = p.locator('article a:has([aria-label="More"])').first
            if a and a.count() > 0:
                href = a.get_attribute("href") or href
        except Exception:
            pass
        p.evaluate("url => navigator.clipboard.writeText(url)", href)
        say("Link copied to clipboard.")
        ig_log("copy_link")
        return True
    except Exception as e:
        ig_log(f"copy_link_error: {e}")
        say("I couldn’t copy the link.")
        return False

def ig_scroll(direction: str = "down", amount: int = 1):
    p = ig_ensure_open()
    if not p:
        return False
    amount = max(1, int(amount))
    try:
        for _ in range(amount):
            if direction == "up":
                p.keyboard.press("PageUp")
            else:
                p.keyboard.press("PageDown")
            time.sleep(0.1)
        say(f"Scrolled {direction}.")
        ig_log(f"scroll_{direction}_{amount}")
        return True
    except Exception as e:
        ig_log(f"scroll_error: {e}")
        return False

def ig_auto_scroll_worker():
    try:
        while IG["auto"]["running"]:
            p = IG["page"] or ig_ensure_open(reels=(IG["auto"]["mode"] == "reels"))
            if not p:
                break
            try:
                if IG["auto"]["mode"] == "reels":
                    p.keyboard.press("PageDown")
                else:
                    p.evaluate("window.scrollBy(0, Math.floor(window.innerHeight*0.9))")
            except Exception:
                pass
            time.sleep(float(IG["auto"]["interval"]))
    except Exception:
        pass

def ig_auto_scroll_start(interval: float = 2.0):
    if IG["auto"]["running"]:
        say("Auto scroll is already running.")
        return False
    IG["auto"]["interval"] = max(0.5, float(interval))
    IG["auto"]["running"] = True
    t = threading.Thread(target=ig_auto_scroll_worker, daemon=True)
    IG["auto"]["thread"] = t
    t.start()
    say(f"Auto scroll started every {int(IG['auto']['interval'])} seconds.")
    ig_log("auto_scroll_start")
    return True

def ig_auto_scroll_stop():
    if not IG["auto"]["running"]:
        return False
    IG["auto"]["running"] = False
    try:
        if IG["auto"]["thread"]:
            IG["auto"]["thread"].join(timeout=0.5)
    except Exception:
        pass
    IG["auto"]["thread"] = None
    say("Auto scroll stopped.")
    ig_log("auto_scroll_stop")
    return True

# -------------------------
# Instagram helpers (default browser control; Playwright optional)
# -------------------------
IG_PROFILE_DIR = os.path.join(os.getcwd(), "ig_profile")
IG = {
    "pw": None,
    "ctx": None,
    "page": None,    # Playwright page if you later enable it
    "auto": {"running": False, "thread": None, "interval": 2.0, "mode": "feed"}
}

def ig_log(msg: str):
    log_action("instagram", msg)

def ig_open_feed():
    say("Opening Instagram...")
    try:
        webbrowser.open("https://www.instagram.com/")
        set_context("instagram")
        IG["auto"]["mode"] = "feed"
        ig_log("open_default_feed")
    except Exception as e:
        log_action("instagram_open_default_error", str(e), "ERROR")

def ig_open_reels():
    say("Opening Instagram Reels...")
    try:
        webbrowser.open("https://www.instagram.com/reels/")
        set_context("instagram")
        IG["auto"]["mode"] = "reels"
        ig_log("open_default_reels")
    except Exception as e:
        log_action("instagram_open_default_error", str(e), "ERROR")

# Double-click center to like (Windows)
if os.name == 'nt':
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    def _double_click_center_of_foreground():
        try:
            hwnd = user32.GetForegroundWindow()
            rect = wintypes.RECT()
            GetWindowRect(hwnd, ctypes.byref(rect))
            cx = int((rect.left + rect.right) / 2)
            cy = int((rect.top + rect.bottom) / 2 * 0.70)
            user32.SetCursorPos(cx, cy)
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.08)
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            return True
        except Exception as e:
            log_action("ig_double_click_error", str(e), "ERROR")
            return False
else:
    def _double_click_center_of_foreground(): return False

def ig_like():
    # Playwright path could go here if you keep it; default-browser fallback:
    focus_app_windows("instagram")
    if _double_click_center_of_foreground():
        say("Liked.")
        ig_log("like_dblclick")
        return True
    say("I couldn’t like it.")
    return False

def ig_unlike():
    say("Unlike is not reliable in default-browser mode. Try Playwright mode for precise control.")
    return False

def ig_next():
    focus_app_windows("instagram")
    press_vk(VK_NEXT)  # PageDown
    say("Next.")
    ig_log("next")
    return True

def ig_prev():
    focus_app_windows("instagram")
    press_vk(VK_PRIOR)  # PageUp
    say("Previous.")
    ig_log("prev")
    return True

def ig_scroll(direction: str = "down", amount: int = 1):
    focus_app_windows("instagram")
    amount = max(1, int(amount))
    for _ in range(amount):
        press_vk(VK_PRIOR if direction == "up" else VK_NEXT)
        time.sleep(0.08)
    say(f"Scrolled {direction}.")
    ig_log(f"scroll_{direction}_{amount}")
    return True

def ig_auto_scroll_worker():
    try:
        while IG["auto"]["running"]:
            focus_app_windows("instagram")
            press_vk(VK_NEXT)
            time.sleep(float(IG["auto"]["interval"]))
    except Exception:
        pass

def ig_auto_scroll_start(interval: float = 2.0):
    if IG["auto"]["running"]:
        say("Auto scroll is already running.")
        return False
    IG["auto"]["interval"] = max(0.5, float(interval))
    IG["auto"]["running"] = True
    t = threading.Thread(target=ig_auto_scroll_worker, daemon=True)
    IG["auto"]["thread"] = t
    t.start()
    say(f"Auto scroll started every {int(IG['auto']['interval'])} seconds.")
    ig_log("auto_scroll_start")
    return True

def ig_auto_scroll_stop():
    if not IG["auto"]["running"]:
        return False
    IG["auto"]["running"] = False
    try:
        if IG["auto"]["thread"]:
            IG["auto"]["thread"].join(timeout=0.5)
    except Exception:
        pass
    IG["auto"]["thread"] = None
    say("Auto scroll stopped.")
    ig_log("auto_scroll_stop")
    return True

def ig_comment(text: str):
    t = (text or "").strip()
    if not t:
        say("What should I comment?")
        return False
    # Best-effort default-browser paste:
    if pyperclip:
        try:
            focus_app_windows("instagram")
            pyperclip.copy(t)
            for _ in range(6):  # tab around a bit
                press_vk(0x09)  # Tab
                time.sleep(0.05)
            press_combo(VK_CONTROL, ord('V'))
            time.sleep(0.05)
            press_vk(0x0D)  # Enter
            say("Comment posted.")
            ig_log("comment_paste")
            return True
        except Exception as e:
            ig_log(f"comment_paste_error: {e}")
    say("I can comment more reliably with Instagram control enabled. Try Playwright mode if needed.")
    return False


# -------------------------
# WhatsApp helpers (Web)
# -------------------------
WHATSAPP_CONTACTS_PATH = "whatsapp_contacts.json"
_WHATSAPP_CONTACTS = None

def load_whatsapp_contacts():
    """Load a simple name->phone map from whatsapp_contacts.json (optional)."""
    global _WHATSAPP_CONTACTS
    if _WHATSAPP_CONTACTS is not None:
        return _WHATSAPP_CONTACTS
    try:
        if os.path.exists(WHATSAPP_CONTACTS_PATH):
            with open(WHATSAPP_CONTACTS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            out = {}
            for k, v in data.items():
                if not k or v is None:
                    continue
                out[str(k).strip().lower()] = str(v).strip()
            _WHATSAPP_CONTACTS = out
            return out
    except Exception as e:
        log_action("whatsapp_contacts_load_error", str(e), "ERROR")
    _WHATSAPP_CONTACTS = {}
    return _WHATSAPP_CONTACTS

def split_contact_and_message(tail: str):
    """
    Try to split 'john doe hello there' into ('john doe', 'hello there')
    by matching known contacts first (prefix match, longest name wins).
    Falls back to first-token split.
    """
    tail = (tail or "").strip()
    if not tail:
        return None, None

    contacts = load_whatsapp_contacts()
    if contacts:
        tail_l = tail.lower()
        # Try longest names first
        for name in sorted(contacts.keys(), key=lambda x: len(x), reverse=True):
            n = name.lower()
            if tail_l == n:
                return tail, ""
            if tail_l.startswith(n + " "):
                return tail[:len(n)], tail[len(n):].strip()
        # fuzzy prefix (use your 'similar' helper on the first len(name) chars)
        best = (None, 0.0)
        for name in contacts.keys():
            n = name.lower()
            prefix = tail_l[:len(n)]
            s = similar(prefix, n)
            if s > best[1]:
                best = (name, s)
        if best[0] and best[1] >= 0.83:
            n = best[0]
            return tail[:len(n)], tail[len(n):].strip()

    # fallback: split first word as name
    parts = tail.split(None, 1)
    if len(parts) >= 2:
        return parts[0], parts[1]
    return tail, ""

def normalize_phone(raw: str) -> str:
    """
    Keep digits only. If it's exactly 10 digits and DEFAULT_COUNTRY_CODE env var is set, prefix it.
    Example: setx DEFAULT_COUNTRY_CODE 91
    """
    digits = re.sub(r'\D', '', raw or '')
    if not digits:
        return ""
    if len(digits) == 10:
        cc = os.getenv("DEFAULT_COUNTRY_CODE")
        if cc and re.fullmatch(r'\d{1,3}', cc):
            digits = cc + digits
    return digits

def whatsapp_send_to_number(phone: str, message: str) -> bool:
    phone_digits = normalize_phone(phone)
    if not phone_digits or len(phone_digits) < 10:
        say("That phone number looks invalid for WhatsApp.")
        log_action("whatsapp_invalid_phone", phone, "WARNING")
        return False
    text = quote_plus(message or "")
    # Directly open WhatsApp Web chat with prefilled text
    url = f"https://web.whatsapp.com/send?phone={phone_digits}&text={text}"
    try:
        webbrowser.open(url)
        say("Opening WhatsApp chat.")
        set_context("whatsapp")
        log_action("whatsapp_open", phone_digits)
        # Try to auto-send with Enter after a short delay (may depend on focus)
        time.sleep(2.5)
        try:
            press_vk(0x0D)  # Enter
        except Exception:
            pass
        return True
    except Exception as e:
        say("I couldn't open WhatsApp.")
        log_action("whatsapp_open_error", str(e), "ERROR")
        return False

def whatsapp_lookup_number(name: str, fuzzy: bool = True, threshold: float = 0.83) -> str:
    """
    Resolve a contact name (case-insensitive) to a phone number from whatsapp_contacts.json.
    Uses fuzzy match if exact name not found.
    """
    contacts = load_whatsapp_contacts()
    if not name:
        return ""
    key = name.strip().lower()
    # exact
    if key in contacts:
        return contacts[key]

    if not fuzzy or not contacts:
        return ""

    # fuzzy: use the existing 'similar' helper (already defined in your file)
    best_name = None
    best_score = 0.0
    for candidate in contacts.keys():
        s = similar(candidate, key)
        if s > best_score:
            best_score, best_name = s, candidate
    if best_name and best_score >= float(threshold):
        return contacts.get(best_name, "")
    return ""

def whatsapp_send_to_contact(contact_name: str, message: str) -> bool:
    phone = whatsapp_lookup_number(contact_name, fuzzy=True)
    if not phone:
        say(f"I couldn't find {contact_name} in WhatsApp contacts. Add it to {WHATSAPP_CONTACTS_PATH}.")
        log_action("whatsapp_contact_missing", contact_name, "WARNING")
        open_app("whatsapp")
        set_context("whatsapp")
        return True
    return whatsapp_send_to_number(phone, message)



def open_search_for_app(app_key: str, query: str):
    if not query:
        open_app(app_key)
        set_context(app_key)
        return
    q = quote_plus(query)
    url = None
    if app_key == "youtube":
        url = f"https://www.youtube.com/results?search_query={q}"
    elif app_key == "youtube_music":
        url = f"https://music.youtube.com/search?q={q}"
    elif app_key in ("google", "chrome", "edge", "firefox", "brave"):
        url = f"https://www.google.com/search?q={q}"
    elif app_key == "spotify":
        url = f"https://open.spotify.com/search/{q}"
    if url:
        try:
            webbrowser.open(url)
            say(f"Searching {query} on {app_key.replace('_', ' ')}.")
            log_action("open_search", f"{app_key}: {query}")
        except Exception as e:
            say(f"I couldn't search on {app_key}.")
            log_action("open_search_error", f"{app_key}: {str(e)}", "ERROR")
    else:
        open_app(app_key)
    set_context(app_key)

def handle_open_phrase(full_phrase: str):
    phrase = full_phrase.strip()
    app_key, matched_alias = resolve_app_from_phrase(phrase)
    if not app_key:
        say("That app is not in the approved list.")
        log_action("open_app_no_match", phrase, "WARNING")
        return

    query = extract_query_after_app(phrase, matched_alias)

    # Spotify: play via API if possible else fallback to search
    if app_key == "spotify" and query:
        if _spotify_play_safe(query):
            return
        say(f"Searching {query} on spotify.")
        q = quote_plus(query)
        webbrowser.open(f"https://open.spotify.com/search/{q}")
        set_context("spotify")
        return

    # YouTube & YouTube Music: try to play top result (your youtube_play_query)
    if app_key in ("youtube", "youtube_music") and query:
        if youtube_play_query(query, prefer_music=(app_key == "youtube_music"), prefer_free=True):
            return
        open_search_for_app(app_key, query)
        return

    # Instagram open in default browser (feed/reels based on query)
    if app_key == "instagram" and query:
        if re.search(r'\breels|\bwheels\b', query, flags=re.I):
            ig_open_reels()
            return

    open_search_for_app(app_key, query)



def parse_play_command(text: str):
    t = text.strip()
    t = re.sub(r'^(and|then|please)\b', '', t, flags=re.I).strip()
    m = re.search(r'\b(play|plays|play me|put on)\b\s+(.+)$', t, flags=re.I)
    if not m:
        return None, None
    rest = m.group(2).strip()
    m2 = re.search(r'(.+?)\s+\b(on|in)\b\s+(.+)$', rest, flags=re.I)
    if m2:
        query = clean_query(m2.group(1))
        app = canonical_app_key(clean_query(m2.group(3)))
        return query or None, app
    return clean_query(rest) or None, None

def parse_search_command(text: str):
    t = text.strip()
    t = re.sub(r'^(and|then|please)\b', '', t, flags=re.I).strip()
    m = re.match(r'^(google|bing)\s+(.+)$', t, flags=re.I)
    if m:
        app = canonical_app_key(m.group(1))
        query = clean_query(m.group(2))
        return query or None, app
    m = re.search(r'\b(search|search for)\b\s+(.+)$', t, flags=re.I)
    if not m:
        return None, None
    rest = m.group(2).strip()
    m2 = re.search(r'(.+?)\s+\b(on|in)\b\s+(.+)$', rest, flags=re.I)
    if m2:
        query = clean_query(m2.group(1))
        app = canonical_app_key(clean_query(m2.group(3)))
        return query or None, app
    return clean_query(rest) or None, None

def supports_search(app_key: str) -> bool:
    return app_key in ("youtube", "youtube_music", "google", "chrome", "edge", "firefox", "brave", "spotify")

def handle_followup_or_intents(text: str) -> bool:
    lc = text.lower().strip()

    # 0) "play second video" / "open 3rd result"
    nth = parse_nth_play_command(lc)
    if nth:
        n, target = nth
        ctx_app = get_recent_context()
        target = target or (ctx_app if ctx_app in ("youtube", "youtube_music") else "youtube")
        if target == "youtube_music":
            say("Numbered selection works for YouTube video results. Say the query like 'play <query> on youtube'.")
            return True
        ctx = get_youtube_results()
        if ctx and ctx.get("items"):
            items = ctx["items"]
            if 1 <= n <= len(items):
                info = items[n-1]
                try:
                    webbrowser.open(info["url"])
                    time.sleep(1.0)
                    press_char('K')
                    say(f"Playing {ordinal_word(n)} result: {info.get('title','')}")
                    set_context("youtube")
                    return True
                except Exception as e:
                    log_action("youtube_open_nth_error", f"n={n} {e}", "ERROR")
            if ctx.get("query"):
                if youtube_play_query(ctx["query"], index=n, prefer_free=True):
                    return True
        say("I don't have recent YouTube results. Say 'play <query> on youtube' first.")
        return True

    # 1) Direct "play ..." intent
    q, app = parse_play_command(lc)
    if q:
        if app == "spotify" or (app is None and get_recent_context() == "spotify"):
            if _spotify_play_safe(q):
                return True
            say(f"Searching {q} on spotify.")
            open_search_for_app("spotify", q)
            return True

        target = app or get_recent_context() or "youtube"
        if target in ("youtube", "youtube_music"):
            if youtube_play_query(q, prefer_music=(target == "youtube_music"), prefer_free=True):
                return True
        if not supports_search(target):
            target = "youtube"
        say(f"Playing {q} on {target.replace('_',' ')}.")
        open_search_for_app(target, q)
        return True

    # 2) Direct "search ..." intent
    q2, app2 = parse_search_command(lc)
    if q2:
        if app2 == "spotify" or (app2 is None and get_recent_context() == "spotify"):
            say(f"Searching {q2} on spotify.")
            open_search_for_app("spotify", q2)
            return True
        target2 = app2 or get_recent_context() or "google"
        if not supports_search(target2):
            target2 = "google"
        say(f"Searching {q2} on {target2.replace('_',' ')}.")
        open_search_for_app(target2, q2)
        return True

    # 3) Follow-ups: "and play ..." / "and search ..."
    if lc.startswith(("and ", "then ", "please ")):
        ctx = get_recent_context()
        if ctx and supports_search(ctx):
            m = re.search(r'\b(play|plays|play me|put on)\b\s+(.+)$', lc, flags=re.I)
            if m:
                q3 = clean_query(m.group(2))
                if q3:
                    if ctx == "spotify":
                        if _spotify_play_safe(q3):
                            return True
                        say(f"Searching {q3} on spotify.")
                        open_search_for_app("spotify", q3)
                        return True
                    if ctx in ("youtube", "youtube_music"):
                        if youtube_play_query(q3, prefer_music=(ctx == "youtube_music"), prefer_free=True):
                            return True
                    say(f"Playing {q3} on {ctx.replace('_',' ')}.")
                    open_search_for_app(ctx, q3)
                    return True
            m = re.search(r'\b(search|search for)\b\s+(.+)$', lc, flags=re.I)
            if m:
                q4 = clean_query(m.group(2))
                if q4:
                    say(f"Searching {q4} on {ctx.replace('_',' ')}.")
                    open_search_for_app(ctx, q4)
                    return True

    return False


# -------------------------
# Volume control (pycaw precise if available; fallback via media keys)
# -------------------------
def volume_get_percent():
    if PYCAW_AVAILABLE:
        try:
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = ctypes.cast(interface, ctypes.POINTER(IAudioEndpointVolume))
            scalar = volume.GetMasterVolumeLevelScalar()
            return int(round(float(scalar) * 100))
        except Exception as e:
            log_action("volume_get_error", str(e), "ERROR")
    return None

def volume_set_percent(pct: int):
    pct = int(max(0, min(100, pct)))
    if PYCAW_AVAILABLE:
        try:
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = ctypes.cast(interface, ctypes.POINTER(IAudioEndpointVolume))
            volume.SetMasterVolumeLevelScalar(float(pct) / 100.0, None)
            say(f"Volume set to {pct} percent.")
            return True
        except Exception as e:
            log_action("volume_set_error", str(e), "ERROR")
    # Fallback: approximate (reset low then ramp up)
    try:
        for _ in range(60):
            press_vk(VK_VOLUME_DOWN)
            time.sleep(0.004)
        steps = int(round(pct / 2))  # ~2% per step
        for _ in range(max(0, steps)):
            press_vk(VK_VOLUME_UP)
            time.sleep(0.004)
        say(f"Volume set to about {pct} percent.")
        return True
    except Exception as e:
        log_action("volume_steps_error", str(e), "ERROR")
        return False

def volume_change_percent(delta: int):
    if PYCAW_AVAILABLE:
        cur = volume_get_percent()
        if cur is None:
            return volume_set_percent(max(0, min(100, 50 + delta)))
        return volume_set_percent(max(0, min(100, cur + delta)))
    steps = int(round(abs(delta) / 2))
    if delta >= 0:
        for _ in range(max(1, steps)):
            press_vk(VK_VOLUME_UP)
            time.sleep(0.01)
        say("Volume up.")
    else:
        for _ in range(max(1, steps)):
            press_vk(VK_VOLUME_DOWN)
            time.sleep(0.01)
        say("Volume down.")
    return True

def parse_int_in_text(text: str, default: int = 5) -> int:
    """
    Extract an integer from text using digits first, then number words.
    Falls back to a small words map or default.
    """
    t = (text or "").lower()

    # digits
    m = re.search(r'\b(\d{1,3})\b', t)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass

    # robust words parser (handles 'twenty five', 'one hundred five')
    n = parse_number_words(t)
    if n is not None:
        return n

    # minimal fallback (single words)
    words_map = {
        "zero":0,"one":1, "two":2, "three":3, "four":4, "five":5,
        "six":6, "seven":7, "eight":8, "nine":9, "ten":10,
        "fifteen":15, "twenty":20, "thirty":30, "forty":40, "fifty":50,
        "sixty":60, "seventy":70, "eighty":80, "ninety":90, "hundred":100
    }
    for w, val in words_map.items():
        if re.search(rf'\b{re.escape(w)}\b', t):
            return val

    return default


def parse_number_words(text: str):
    """
    Convert number words in text to an int (e.g., 'ninety five' -> 95,
    'one hundred five' -> 105). Returns None if no number words found.
    """
    t = (text or "").lower()
    t = t.replace("-", " ")
    tokens = re.findall(r'\w+', t)

    units = {
        "zero":0, "one":1, "two":2, "three":3, "four":4, "five":5,
        "six":6, "seven":7, "eight":8, "nine":9, "ten":10,
        "eleven":11, "twelve":12, "thirteen":13, "fourteen":14, "fifteen":15,
        "sixteen":16, "seventeen":17, "eighteen":18, "nineteen":19
    }
    tens = {
        "twenty":20, "thirty":30, "forty":40, "fifty":50,
        "sixty":60, "seventy":70, "eighty":80, "ninety":90
    }
    scales = {"hundred":100}

    current = 0
    total = 0
    found = False

    for tok in tokens:
        if tok in units:
            current += units[tok]
            found = True
        elif tok in tens:
            current += tens[tok]
            found = True
        elif tok in scales:
            # e.g., "hundred" or "one hundred"
            if current == 0:
                current = 1
            current *= scales[tok]
            found = True
        elif tok == "and":
            continue
        else:
            # ignore unrelated words
            continue

    total += current
    return total if found else None

def parse_instagram_command(text: str):
    t = (text or "").lower().strip()
    ctx = get_recent_context()
    mentions_ig = ("instagram" in t or "insta" in t or "ig " in t or ctx == "instagram")
    words = re.findall(r'\w+', t)
    fuzzy_reels = any(similar(w, "reels") >= 0.8 for w in words)

    if re.search(r'\b(open|launch|start|go to|navigate to)\b\s+(instagram|insta|ig)(\s+reels|\s+wheels)?', t):
        reels = fuzzy_reels or bool(re.search(r'\breels\b', t))
        return {"action": "open_reels" if reels else "open_feed"}

    if mentions_ig and re.search(r'\b(open)\b.*\b(reels)\b', t):
        return {"action": "open_reels"}

    if mentions_ig and re.search(r'\b(unlike|remove like|dislike)\b', t):
        return {"action": "unlike"}
    if mentions_ig and re.search(r'\b(like|heart)\b', t):
        return {"action": "like"}

    if mentions_ig and re.search(r'\b(next|skip)\b', t):
        return {"action": "next"}
    if mentions_ig and re.search(r'\b(previous|prev|back)\b', t):
        return {"action": "prev"}

    m = re.search(r'\bcomment\b\s+(.+)$', t)
    if mentions_ig and m:
        return {"action": "comment", "text": m.group(1).strip()}

    m2 = re.search(r'\bscroll\s+(up|down)\b', t)
    if mentions_ig and m2:
        dirn = m2.group(1)
        amt = parse_int_in_text(t, default=1)
        return {"action": "scroll", "dir": dirn, "amt": amt}

    if mentions_ig and re.search(r'\b(auto\s*scroll)\b', t):
        if re.search(r'\b(stop|off|disable)\b', t):
            return {"action": "auto_scroll_stop"}
        val = parse_int_in_text(t, default=2)
        return {"action": "auto_scroll_start", "interval": val}

    return None

def handle_instagram_command(cmd: dict) -> bool:
    if not cmd:
        return False
    a = cmd.get("action")
    if a == "open_feed":
        ig_open_feed(); return True
    if a == "open_reels":
        ig_open_reels(); return True
    if a == "like":
        return ig_like()
    if a == "unlike":
        return ig_unlike()
    if a == "next":
        return ig_next()
    if a == "prev":
        return ig_prev()
    if a == "comment":
        return ig_comment(cmd.get("text",""))
    if a == "scroll":
        return ig_scroll(cmd.get("dir","down"), max(1, int(cmd.get("amt",1))))
    if a == "auto_scroll_start":
        return ig_auto_scroll_start(float(cmd.get("interval",2)))
    if a == "auto_scroll_stop":
        return ig_auto_scroll_stop()
    return False

def parse_window_command(text: str):
    t = (text or "").lower().strip()

    if re.search(r'\b(minimi[sz]e\s+all|show\s+desktop)\b', t):
        return {"action": "minimize_all"}

    # Accept 'window' or 'windows'
    if re.search(r'\b(minimi[sz]e|maximi[sz]e|restore)\s+(this\s+)?(window|windows|tab)\b', t):
        act = "minimize" if "minimi" in t else "maximize" if "maximi" in t else "restore"
        return {"action": act, "app_key": None, "scope": "active"}

    if re.search(r'\b(focus|bring|switch)\s+(this\s+)?(window|windows|tab)\b', t):
        return {"action": "focus", "app_key": None, "scope": "active"}

    m = re.match(r'^(minimi[sz]e|maximi[sz]e|restore|focus|bring|switch\s+to)\s+(.+)$', t)
    if m:
        action_raw = m.group(1)
        name = m.group(2).strip()
        action = "focus" if action_raw in ("focus","bring") or action_raw.startswith("switch") else \
                 "minimize" if action_raw.startswith("minimi") else \
                 "maximize" if action_raw.startswith("maximi") else "restore"
        app_key = canonical_app_key(name) or name.lower()
        return {"action": action, "app_key": app_key, "scope": "app"}

    m2 = re.match(r'^(minimi[sz]e)\s+(.+)$', t)
    if m2:
        app_key = canonical_app_key(m2.group(2).strip()) or m2.group(2).strip().lower()
        return {"action": "minimize", "app_key": app_key, "scope": "app"}

    return None

def handle_window_command(cmd: dict) -> bool:
    if not cmd:
        return False
    action = cmd.get("action")
    app_key = cmd.get("app_key")
    scope = cmd.get("scope")

    if action == "minimize_all":
        return minimize_all_windows()

    if scope == "active" or (not app_key and action in ("minimize","maximize","restore","focus")):
        if action == "minimize":
            return minimize_active_window()
        if action == "maximize":
            try:
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    _restore_hwnd(hwnd); time.sleep(0.05); _maximize_hwnd(hwnd)
                    say("Maximized window."); return True
            except Exception: pass
            say("I couldn't maximize the active window."); return False
        if action == "restore":
            try:
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    _restore_hwnd(hwnd); say("Restored window."); return True
            except Exception: pass
            say("I couldn't restore the active window."); return False
        if action == "focus":
            say("Already focused."); return True

    if not app_key:
        return False
    if action == "minimize":
        return minimize_app_windows(app_key)
    if action == "maximize":
        return maximize_app_windows(app_key)
    if action == "restore":
        return restore_app_windows(app_key)
    if action == "focus":
        return focus_app_windows(app_key)
    return False


def parse_number_any(text: str):
    """
    Return an int if the text contains a number (digits or words), else None.
    Handles '90', '90%', 'ninety five', 'one hundred', etc.
    """
    t = (text or "").lower()
    m = re.search(r'\b(\d{1,3})\s*%?\b', t)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    return parse_number_words(t)

def parse_volume_command(text: str):
    t = text.lower().strip()

    if not re.search(r'\bvolume\b', t):
        return None

    # Increase/decrease first
    if re.search(r'\b(increase|raise)\s+volume\b', t) or re.search(r'\bvolume\s+up\b', t):
        delta = parse_int_in_text(t, default=10)
        return {"action": "volume_change", "delta": abs(delta)}
    if re.search(r'\b(decrease|lower|reduce)\s+volume\b', t) or re.search(r'\bvolume\s+down\b', t):
        delta = parse_int_in_text(t, default=10)
        return {"action": "volume_change", "delta": -abs(delta)}

    # Set volume to N (digits or words)
    # Matches: 'set volume to 60', 'volume 60 percent', 'volume sixty five percent'
    m = re.search(r'\b(set|change|adjust)\s+volume\s+(?:to\s+)?(\d{1,3})\s*%?\b', t)
    if m:
        return {"action": "volume_set", "value": int(m.group(2))}
    m2 = re.search(r'\bvolume\s+(?:to\s+)?(\d{1,3})\s*%?\b', t)
    if m2:
        return {"action": "volume_set", "value": int(m2.group(1))}

    # Word-number fallback: 'volume ninety five', 'set volume to eighty'
    if re.search(r'\b(set|change|adjust|volume)\b', t) and not re.search(r'\b(up|down|increase|raise|decrease|lower|reduce)\b', t):
        n = parse_number_any(t)
        if n is not None:
            return {"action": "volume_set", "value": int(n)}

    return None


# -------------------------
# Brightness control (WMI via PowerShell)
# -------------------------
def brightness_get_percent():
    ps = "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness | Select-Object -First 1 -ExpandProperty CurrentBrightness)"
    rc, out, err = run_ps(ps)
    if rc == 0 and out:
        try:
            val = int(re.findall(r'\d+', out)[0])
            return max(0, min(100, val))
        except Exception as e:
            log_action("brightness_get_parse_error", str(e), "ERROR")
    log_action("brightness_get_error", err or out, "ERROR")
    return None

def brightness_set_percent(pct: int):
    pct = int(max(0, min(100, pct)))
    ps = f"$b={pct}; $w=(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods); foreach($m in $w){{ $m.WmiSetBrightness(1,$b) }};"
    rc, out, err = run_ps(ps)
    if rc == 0:
        say(f"Brightness set to {pct} percent.")
        return True
    say("I couldn't set the brightness.")
    log_action("brightness_set_error", err or out, "ERROR")
    return False

def brightness_change_percent(delta: int):
    cur = brightness_get_percent()
    if cur is None:
        target = max(0, min(100, 50 + delta))
    else:
        target = max(0, min(100, cur + delta))
    return brightness_set_percent(target)

def parse_brightness_command(text: str):
    t = text.lower().strip()

    if "brightness" not in t:
        return None

    # Increase/decrease first
    if re.search(r'\b(increase|raise)\s+brightness\b', t):
        delta = parse_int_in_text(t, default=10)
        return {"action": "brightness_change", "delta": abs(delta)}
    if re.search(r'\b(decrease|lower|reduce)\s+brightness\b', t):
        delta = parse_int_in_text(t, default=10)
        return {"action": "brightness_change", "delta": -abs(delta)}

    # Set brightness to N (digits)
    m = re.search(r'\b(set|change|adjust)\s+brightness\s+(?:to\s+)?(\d{1,3})\s*%?\b', t)
    if m:
        return {"action": "brightness_set", "value": int(m.group(2))}
    m2 = re.search(r'\bbrightness\s+(?:to\s+)?(\d{1,3})\s*%?\b', t)
    if m2:
        return {"action": "brightness_set", "value": int(m2.group(1))}

    # Word-number fallback: 'brightness sixty five percent'
    if re.search(r'\b(set|change|adjust|brightness)\b', t) and not re.search(r'\b(up|down|increase|raise|decrease|lower|reduce)\b', t):
        n = parse_number_any(t)
        if n is not None:
            return {"action": "brightness_set", "value": int(n)}

    return None

# -------------------------
# Global media controls + YouTube specifics
# -------------------------

def _focus_if_app(app_key: str):
    """
    Bring an app/tab to foreground before sending keys.
    Requires focus_app_windows from the window helpers (Windows).
    No-op if not available.
    """
    try:
        if os.name == 'nt' and app_key and 'focus_app_windows' in globals():
            focus_app_windows(app_key)
            time.sleep(0.05)
    except Exception:
        pass

def media_play_pause(app: str = None):
    if app in ("youtube", "youtube_music"):
        _focus_if_app(app)
        press_char('K')  # YouTube play/pause
        say("Toggled play/pause on YouTube.")
    else:
        press_vk(VK_MEDIA_PLAY_PAUSE)
        say("Toggled play/pause.")

def media_next():
    press_vk(VK_MEDIA_NEXT_TRACK)
    say("Next track.")

def media_prev():
    press_vk(VK_MEDIA_PREV_TRACK)
    say("Previous track.")

def media_stop():
    press_vk(VK_MEDIA_STOP)
    say("Stopped playback.")

def media_mute():
    press_vk(VK_VOLUME_MUTE)
    say("Muted.")

def media_unmute():
    press_vk(VK_VOLUME_MUTE)
    time.sleep(0.05)
    press_vk(VK_VOLUME_MUTE)
    say("Unmuted.")

def volume_up_steps(steps=2):
    for _ in range(max(1, steps)):
        press_vk(VK_VOLUME_UP)
        time.sleep(0.02)
    say("Volume up.")

def volume_down_steps(steps=2):
    for _ in range(max(1, steps)):
        press_vk(VK_VOLUME_DOWN)
        time.sleep(0.02)
    say("Volume down.")

def youtube_seek(direction: str, seconds: int):
    _focus_if_app("youtube")
    if seconds <= 0:
        return
    if seconds % 10 == 0:
        times = seconds // 10
        key = 'L' if direction == "forward" else 'J'
        for _ in range(times):
            press_char(key)
            time.sleep(0.04)
    else:
        times = max(1, int(round(seconds / 5.0)))
        vk = VK_RIGHT if direction == "forward" else VK_LEFT
        for _ in range(times):
            press_vk(vk)
            time.sleep(0.04)

def youtube_fullscreen(on=True):
    _focus_if_app("youtube")
    press_char('F')
    say("Fullscreen" if on else "Exit fullscreen")

def generic_seek(direction: str, seconds: int):
    vk = VK_RIGHT if direction == "forward" else VK_LEFT
    times = max(1, int(round(seconds / 5.0)))
    for _ in range(times):
        press_vk(vk)
        time.sleep(0.04)
    say(f"Seek {direction} {seconds} seconds.")

def parse_media_control(text: str):
    t = text.lower().strip()
    app = None
    m = re.search(r'\b(on|in)\b\s+([a-zA-Z ]+)$', t)
    if m:
        app = canonical_app_key(m.group(2).strip()) or None

    if re.search(r'\b(play\s*pause|play\/pause|toggle|toggle play|toggle pause)\b', t):
        return {"action": "play_pause", "app": app}
    if re.search(r'\b(next|skip)\b', t):
        return {"action": "next", "app": app}
    if re.search(r'\b(previous|prev|back track)\b', t):
        return {"action": "previous", "app": app}
    if re.search(r'\bstop\b', t):
        return {"action": "stop", "app": app}
    if re.search(r'\bmute\b', t):
        return {"action": "mute", "app": app}
    if re.search(r'\bunmute\b', t):
        return {"action": "unmute", "app": app}
    if re.search(r'\b(volume up|increase volume|louder)\b', t):
        steps = parse_int_in_text(t, default=2)
        return {"action": "volume_up_steps", "steps": steps, "app": app}
    if re.search(r'\b(volume down|decrease volume|softer|lower)\b', t):
        steps = parse_int_in_text(t, default=2)
        return {"action": "volume_down_steps", "steps": steps, "app": app}
    if re.search(r'\b(fullscreen|full screen)\b', t):
        return {"action": "fullscreen", "app": app}
    if re.search(r'\b(exit fullscreen|exit full screen|leave fullscreen)\b', t):
        return {"action": "exit_fullscreen", "app": app}

    # single-word media toggles only
    if re.fullmatch(r'\s*(pause)\s*', t):
        return {"action": "play_pause", "app": app}
    if re.fullmatch(r'\s*(resume|continue|play)\s*', t):
        return {"action": "play_pause", "app": app}

    if re.search(r'\bseek forward|forward\b', t):
        secs = parse_int_in_text(t, default=10)
        return {"action": "seek_forward", "app": app, "seconds": secs}
    if re.search(r'\bseek back|seek backward|backward|rewind\b', t):
        secs = parse_int_in_text(t, default=10)
        return {"action": "seek_back", "app": app, "seconds": secs}

    return None

def handle_media_control(cmd: dict):
    action = (cmd or {}).get("action")
    app = (cmd or {}).get("app") or get_recent_context()
    app = app if app in CONFIG["app_paths"] else None

    # Auto-focus target app/tab if known (pre-opened control)
    if app in ("youtube", "youtube_music", "instagram", "spotify", "chrome", "edge", "firefox", "brave"):
        _focus_if_app(app)

    if action == "play_pause":
        media_play_pause(app if app else None)
        return True

    if action == "next":
        # YouTube/YouTube Music use Shift+N (next video)
        if app in ("youtube", "youtube_music"):
            press_combo(VK_SHIFT, ord('N'))
            say("Next video.")
        else:
            media_next()
        return True

    if action == "previous":
        # YouTube/YouTube Music use Shift+P (previous video)
        if app in ("youtube", "youtube_music"):
            press_combo(VK_SHIFT, ord('P'))
            say("Previous video.")
        else:
            media_prev()
        return True

    if action == "stop":
        media_stop()
        return True
    if action == "mute":
        media_mute()
        return True
    if action == "unmute":
        media_unmute()
        return True
    if action == "volume_up_steps":
        volume_up_steps(max(1, int(cmd.get("steps", 2))))
        return True
    if action == "volume_down_steps":
        volume_down_steps(max(1, int(cmd.get("steps", 2))))
        return True
    if action in ("fullscreen", "exit_fullscreen"):
        if (app or "") in ("youtube", "youtube_music"):
            youtube_fullscreen(on=(action == "fullscreen"))
        else:
            press_combo(0x12, 0x0D)  # ALT+ENTER
            say("Toggled fullscreen.")
        return True
    if action == "seek_forward":
        secs = max(1, int(cmd.get("seconds", 10)))
        if (app or "") in ("youtube", "youtube_music"):
            youtube_seek("forward", secs)
        else:
            generic_seek("forward", secs)
        return True
    if action == "seek_back":
        secs = max(1, int(cmd.get("seconds", 10)))
        if (app or "") in ("youtube", "youtube_music"):
            youtube_seek("back", secs)
        else:
            generic_seek("back", secs)
        return True
    return False


# -------------------------
# Scroll helpers
# -------------------------
def scroll_lines(direction: str, amount: int = 10):
    if direction == "up":
        mouse_scroll(amount)
        say(f"Scrolling up.")
    elif direction == "down":
        mouse_scroll(-amount)
        say(f"Scrolling down.")

def page_scroll(direction: str):
    if direction == "up":
        press_vk(0x21)  # Page Up
        say("Page up.")
    elif direction == "down":
        press_vk(0x22)  # Page Down
        say("Page down.")

def scroll_to(position: str):
    if position == "top":
        press_vk(0x24)  # Home
        say("Scrolled to top.")
    elif position == "bottom":
        press_vk(0x23)  # End
        say("Scrolled to bottom.")

def parse_scroll_command(text: str):
    t = text.lower().strip()
    if re.search(r'\b(scroll|go|jump)\s+(to\s+)?top\b', t):
        return {"action": "scroll_to", "pos": "top"}
    if re.search(r'\b(scroll|go|jump)\s+(to\s+)?bottom\b', t):
        return {"action": "scroll_to", "pos": "bottom"}
    if re.search(r'\b(page up|pg up)\b', t):
        return {"action": "page", "dir": "up"}
    if re.search(r'\b(page down|pg down)\b', t):
        return {"action": "page", "dir": "down"}
    if re.search(r'\bscroll (up|down)\b', t):
        m = re.search(r'\bscroll (up|down)\b', t)
        direction = m.group(1)
        amt = parse_int_in_text(t, default=10)
        if re.search(r'\b(little|small|slightly|a bit)\b', t):
            amt = max(3, min(amt, 5))
        return {"action": "lines", "dir": direction, "amt": amt}
    if re.search(r'\bscroll\b', t):
        amt = parse_int_in_text(t, default=10)
        return {"action": "lines", "dir": "down", "amt": amt}
    return None

def handle_scroll(cmd: dict) -> bool:
    if not cmd:
        return False
    if cmd["action"] == "scroll_to":
        scroll_to(cmd["pos"])
        return True
    if cmd["action"] == "page":
        page_scroll(cmd["dir"])
        return True
    if cmd["action"] == "lines":
        scroll_lines(cmd["dir"], max(1, int(cmd["amt"])))
        return True
    return False

def handle_whatsapp_command(cmd: dict) -> bool:
    if not cmd or cmd.get("action") != "whatsapp_send":
        return False
    msg = (cmd.get("message") or "").strip()

    # Prefer contact name
    contact_name = (cmd.get("contact") or "").strip()
    if contact_name:
        if not msg:
            say(f"What is the message for {contact_name}?")
            return False
        return whatsapp_send_to_contact(contact_name, msg)

    # Fallback to explicit phone number if they used one
    phone = (cmd.get("target") or "").strip()
    if phone:
        if not msg:
            say("What is the message?")
            return False
        return whatsapp_send_to_number(phone, msg)

    return False

    return False

# -------------------------
# Window management helpers (Windows only)
# -------------------------
if os.name == 'nt':
    SW_HIDE = 0
    SW_SHOWNORMAL = 1
    SW_SHOWMINIMIZED = 2
    SW_MAXIMIZE = 3
    SW_SHOW = 5
    SW_MINIMIZE = 6
    SW_RESTORE = 9
    VK_LWIN = 0x5B

    # ctypes signatures
    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    IsWindowVisible = user32.IsWindowVisible
    GetWindowTextW = user32.GetWindowTextW
    GetWindowTextLengthW = user32.GetWindowTextLengthW
    GetClassNameW = user32.GetClassNameW
    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    ShowWindowAsync = user32.ShowWindowAsync
    SetForegroundWindow = user32.SetForegroundWindow
    GetForegroundWindow = user32.GetForegroundWindow

    def _get_window_text(hwnd):
        try:
            length = GetWindowTextLengthW(hwnd)
            if length == 0:
                return ""
            buf = ctypes.create_unicode_buffer(length + 1)
            GetWindowTextW(hwnd, buf, length + 1)
            return buf.value
        except Exception:
            return ""

    def _enum_top_windows():
        """Return list of dicts: {hwnd, title, pid, visible}"""
        results = []

        def _callback(hwnd, lParam):
            try:
                if not IsWindowVisible(hwnd):
                    return True
                pid = ctypes.c_ulong()
                GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                title = _get_window_text(hwnd) or ""
                results.append({"hwnd": hwnd, "pid": int(pid.value), "title": title})
            except Exception:
                pass
            return True

        EnumWindows(EnumWindowsProc(_callback), 0)
        return results

    def _proc_name(pid):
        # Prefer psutil if available
        try:
            if psutil:
                return (psutil.Process(pid).name() or "").lower()
        except Exception:
            pass
        return ""

    def _base_exe_for_app(app_key: str) -> str:
        """Return base exe name (e.g., chrome.exe) for local apps. Empty for web URLs."""
        app_paths = CONFIG.get("app_paths", {})
        target = app_paths.get(app_key) or ""
        if target.startswith(("http://", "https://", "ms-settings:", "ms-")):
            return ""
        base = target.split()[0].strip('"')
        return os.path.basename(base).lower()

    def _title_keywords_for_web(app_key: str):
        """Keywords to match browser window title for web apps."""
        mapping = {
            "youtube": ["youtube"],
            "youtube_music": ["youtube music", "yt music"],
            "instagram": ["instagram"],
            "facebook": ["facebook"],
            "whatsapp": ["whatsapp"],
            "google": ["google"],
            "bing": ["bing"],
            "chatgpt": ["chatgpt", "openai"]
        }
        return [w.lower() for w in mapping.get(app_key, [])]

    def _windows_for_app(app_key: str):
        """Find top-level windows for a given app by process name or title keywords."""
        wins = _enum_top_windows()
        exe = _base_exe_for_app(app_key)
        web = exe == ""
        want_titles = _title_keywords_for_web(app_key) if web else []
        matches = []

        for w in wins:
            title = (w.get("title") or "").lower()
            name = _proc_name(w.get("pid", 0))
            if not web:
                # local app: match process image name
                if name and exe and name == exe:
                    matches.append(w)
            else:
                # web app: prefer title keyword match
                if any(k in title for k in want_titles) and title:
                    matches.append(w)

        # If no matches for web, fallback to foreground window (likely the browser used)
        if web and not matches:
            try:
                fg = GetForegroundWindow()
                if fg:
                    matches.append({"hwnd": fg, "title": _get_window_text(fg), "pid": 0})
            except Exception:
                pass
        return matches

    def _minimize_hwnd(hwnd):
        try:
            ShowWindowAsync(hwnd, SW_MINIMIZE)
            return True
        except Exception:
            return False

    def _maximize_hwnd(hwnd):
        try:
            ShowWindowAsync(hwnd, SW_MAXIMIZE)
            return True
        except Exception:
            return False

    def _restore_hwnd(hwnd):
        try:
            ShowWindowAsync(hwnd, SW_RESTORE)
            return True
        except Exception:
            return False

    def _focus_hwnd(hwnd):
        try:
            _restore_hwnd(hwnd)
            time.sleep(0.05)
            SetForegroundWindow(hwnd)
            return True
        except Exception:
            return False

    def minimize_app_windows(app_key: str) -> bool:
        wins = _windows_for_app(app_key)
        ok = False
        for w in wins:
            ok = _minimize_hwnd(w["hwnd"]) or ok
        if ok:
            say(f"Minimized {app_key.replace('_',' ')}.")
            log_action("win_minimize", app_key)
        else:
            say(f"I couldn't find a window for {app_key.replace('_',' ')}.")
            log_action("win_minimize_not_found", app_key, "WARNING")
        return ok

    def maximize_app_windows(app_key: str) -> bool:
        wins = _windows_for_app(app_key)
        ok = False
        for w in wins:
            ok = _maximize_hwnd(w["hwnd"]) or ok
        if ok:
            say(f"Maximized {app_key.replace('_',' ')}.")
            log_action("win_maximize", app_key)
        else:
            say(f"I couldn't find a window for {app_key.replace('_',' ')}.")
            log_action("win_maximize_not_found", app_key, "WARNING")
        return ok

    def restore_app_windows(app_key: str) -> bool:
        wins = _windows_for_app(app_key)
        ok = False
        for w in wins:
            ok = _restore_hwnd(w["hwnd"]) or ok
        if ok:
            say(f"Restored {app_key.replace('_',' ')}.")
            log_action("win_restore", app_key)
        else:
            say(f"I couldn't find a window for {app_key.replace('_',' ')}.")
            log_action("win_restore_not_found", app_key, "WARNING")
        return ok

    def focus_app_windows(app_key: str) -> bool:
        wins = _windows_for_app(app_key)
        if wins:
            _focus_hwnd(wins[0]["hwnd"])
            say(f"Switched to {app_key.replace('_',' ')}.")
            log_action("win_focus", app_key)
            return True
        say(f"I couldn't find a window for {app_key.replace('_',' ')}.")
        log_action("win_focus_not_found", app_key, "WARNING")
        return False

    def _focus_if_app(app_key: str):
        try:
            if os.name == 'nt' and app_key:
                focus_app_windows(app_key)
                time.sleep(0.05)
        except Exception:
            pass


    def minimize_active_window():
        try:
            hwnd = GetForegroundWindow()
            if hwnd:
                _minimize_hwnd(hwnd)
                say("Minimized window.")
                log_action("win_minimize_active", "")
                return True
        except Exception:
            pass
        say("I couldn't minimize the active window.")
        return False

    def minimize_all_windows():
        # Win + D toggles show desktop
        try:
            press_combo(VK_LWIN, ord('D'))
            say("Minimized all windows.")
            log_action("win_minimize_all", "")
            return True
        except Exception as e:
            log_action("win_minimize_all_error", str(e), "ERROR")
            return False
else:
    # Non-Windows stubs
    def minimize_app_windows(app_key: str): say("Window control is only on Windows."); return False
    def maximize_app_windows(app_key: str): say("Window control is only on Windows."); return False
    def restore_app_windows(app_key: str):  say("Window control is only on Windows."); return False
    def focus_app_windows(app_key: str):    say("Window control is only on Windows."); return False
    def minimize_active_window():           say("Window control is only on Windows."); return False
    def minimize_all_windows():             say("Window control is only on Windows."); return False

# -------------------------
# Wi-Fi helpers (Windows)
# -------------------------
def wifi_enable():
    if os.name != 'nt':
        say("Wi-Fi control is only implemented on Windows.")
        return
    if not confirm_action("Turn on Wi-Fi", require_auth=True):
        say("Wi-Fi enable cancelled.")
        return
    rc, out, err = run_cmd(["netsh", "interface", "set", "interface", "name=Wi-Fi", "admin=enabled"])
    if rc == 0:
        say("Wi-Fi turned on.")
        log_action("wifi_enable")
    else:
        say("I couldn't turn on Wi-Fi.")
        log_action("wifi_enable_error", err or out, "ERROR")

def wifi_disable():
    if os.name != 'nt':
        say("Wi-Fi control is only implemented on Windows.")
        return
    if not confirm_action("Turn off Wi-Fi", require_auth=True):
        say("Wi-Fi disable cancelled.")
        return
    rc, out, err = run_cmd(["netsh", "interface", "set", "interface", "name=Wi-Fi", "admin=disabled"])
    if rc == 0:
        say("Wi-Fi turned off.")
        log_action("wifi_disable")
    else:
        say("I couldn't turn off Wi-Fi.")
        log_action("wifi_disable_error", err or out, "ERROR")

def wifi_connect(ssid: str):
    if os.name != 'nt':
        say("Wi-Fi connect is only implemented on Windows.")
        return
    ssid_s = ssid.strip().replace('"', '')
    if not ssid_s:
        say("That network name looks invalid.")
        log_action("wifi_connect_invalid_ssid", ssid, "WARNING")
        return
    if not confirm_action(f"Connect to Wi-Fi network {ssid_s}", require_auth=True):
        say("Connect cancelled.")
        return
    rc, out, err = run_cmd(["netsh", "wlan", "connect", f"name={ssid_s}"])
    if rc == 0:
        say(f"Connecting to {ssid_s}.")
        log_action("wifi_connect", ssid_s)
    else:
        say(f"I couldn't connect to {ssid_s}. Make sure a profile exists.")
        log_action("wifi_connect_error", f"{ssid_s}: {err or out}", "ERROR")

def wifi_disconnect():
    if os.name != 'nt':
        say("Wi-Fi control is only implemented on Windows.")
        return
    if not confirm_action("Disconnect Wi-Fi", require_auth=True):
        say("Disconnect cancelled.")
        return
    rc, out, err = run_cmd(["netsh", "wlan", "disconnect"])
    if rc == 0:
        say("Disconnected Wi-Fi.")
        log_action("wifi_disconnect")
    else:
        say("I couldn't disconnect Wi-Fi.")
        log_action("wifi_disconnect_error", err or out, "ERROR")

# -------------------------
# Bluetooth helpers (PowerShell wrappers)
# -------------------------
def bluetooth_enable():
    if os.name != 'nt':
        say("Bluetooth control is only implemented on Windows.")
        return
    if not confirm_action("Turn on Bluetooth", require_auth=True):
        say("Bluetooth enable cancelled.")
        return
    ps = r"""
    $adapters = Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue
    if ($adapters) {
        foreach ($a in $adapters) {
            if ($a.Status -ne 'OK') { Enable-PnpDevice -InstanceId $a.InstanceId -Confirm:$false -ErrorAction SilentlyContinue }
        }
    }
    """
    rc, out, err = run_ps(ps)
    if rc == 0:
        say("Bluetooth turned on.")
        log_action("bluetooth_enable")
    else:
        say("I couldn't turn on Bluetooth.")
        log_action("bluetooth_enable_error", err or out, "ERROR")

def bluetooth_disable():
    if os.name != 'nt':
        say("Bluetooth control is only implemented on Windows.")
        return
    if not confirm_action("Turn off Bluetooth", require_auth=True):
        say("Bluetooth disable cancelled.")
        return
    ps = r"""
    $adapters = Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue
    if ($adapters) {
        foreach ($a in $adapters) {
            if ($a.Status -eq 'OK') { Disable-PnpDevice -InstanceId $a.InstanceId -Confirm:$false -ErrorAction SilentlyContinue }
        }
    }
    """
    rc, out, err = run_ps(ps)
    if rc == 0:
        say("Bluetooth turned off.")
        log_action("bluetooth_disable")
    else:
        say("I couldn't turn off Bluetooth.")
        log_action("bluetooth_disable_error", err or out, "ERROR")

def list_bluetooth_devices():
    if os.name != 'nt':
        say("Bluetooth list is only implemented on Windows.")
        return []
    ps = "Get-PnpDevice -Class Bluetooth | Select-Object -Property FriendlyName,InstanceId,Status | ConvertTo-Json -Depth 3"
    rc, out, err = run_ps(ps)
    if rc == 0 and out:
        try:
            items = json.loads(out)
            if isinstance(items, dict):
                items = [items]
            devices = []
            for it in items:
                name = it.get("FriendlyName") or it.get("InstanceId") or "Unknown"
                devices.append({"name": name, "status": it.get("Status")})
            log_action("bluetooth_list", f"found {len(devices)} devices")
            return devices
        except Exception as e:
            log_action("bluetooth_list_parse_error", str(e), "ERROR")
    return []

# -------------------------
# Battery helper
# -------------------------
def battery_status():
    try:
        if psutil:
            try:
                batt = psutil.sensors_battery()
            except Exception:
                batt = None
            if batt:
                percent = int(batt.percent)
                plugged = batt.power_plugged
                say(f"Battery at {percent} percent. {'Plugged in' if plugged else 'Not plugged in'}.")
                log_action("battery_status", str(percent))
                return
        if os.name == 'nt':
            rc, out, err = run_cmd(["WMIC", "PATH", "Win32_Battery", "Get", "EstimatedChargeRemaining", "/VALUE"])
            if rc == 0 and out:
                nums = re.findall(r"\d+", out)
                if nums:
                    p = int(nums[0])
                    say(f"Battery at {p} percent.")
                    log_action("battery_status", str(p))
                    return
        say("I couldn't read the battery status.")
        log_action("battery_status_error", "no battery", "WARNING")
    except Exception as e:
        say("I couldn't read the battery status.")
        log_action("battery_status_error", str(e), "ERROR")

# -------------------------
# Audio helpers
# -------------------------
def save_wav(path: str, data, samplerate: int = DEFAULT_SAMPLE_RATE):
    try:
        if np is not None and isinstance(data, np.ndarray):
            if data.dtype != np.int16:
                scaled = np.clip(data.astype(np.float32), -1.0, 1.0)
                ints = (scaled * 32767.0).astype(np.int16)
            else:
                ints = data
            raw = ints.tobytes()
        else:
            floats = []
            try:
                for x in data:
                    try:
                        fx = float(x)
                    except Exception:
                        fx = 0.0
                    fx = max(-1.0, min(1.0, fx))
                    floats.append(int(fx * 32767.0))
            except TypeError:
                floats = [0]
            import struct
            raw = b"".join(struct.pack('<h', i) for i in floats)

        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(samplerate)
            wf.writeframes(raw)
    except Exception as e:
        log_action("save_wav_error", str(e), "ERROR")

def record_audio(seconds: int = 4, samplerate: int = DEFAULT_SAMPLE_RATE):
    if sd is None:
        return None
    try:
        device_index = None
        try:
            if hasattr(sd, "default") and isinstance(sd.default.device, (list, tuple)) and sd.default.device:
                di = sd.default.device[0]
                if di is not None and di >= 0:
                    device_index = di
            if device_index is None:
                for idx, dev in enumerate(sd.query_devices()):
                    if dev.get('max_input_channels', 0) > 0:
                        device_index = idx
                        break
        except Exception:
            device_index = None

        data = sd.rec(int(seconds * samplerate), samplerate=samplerate, channels=1, dtype='float32', device=device_index)
        sd.wait()
        if np is not None:
            return data.flatten()
        else:
            return [float(x[0]) for x in data]
    except Exception as e:
        log_action("audio_record_error", str(e), "ERROR")
        return None

# -------------------------
# VAD helpers (RMS gating)
# -------------------------
NOISE_RMS = None
def compute_rms(arr):
    try:
        if np is not None:
            a = np.asarray(arr, dtype=np.float32)
            return float(np.sqrt(np.mean(a * a))) if a.size else 0.0
        s = 0.0
        n = 0
        for x in arr:
            s += float(x) * float(x)
            n += 1
        return (s / n) ** 0.5 if n else 0.0
    except Exception:
        return 0.0

def calibrate_noise(seconds: float):
    print("Calibrating mic noise... please stay quiet.")
    time.sleep(0.8)
    global NOISE_RMS
    frames = []
    chunk = 0.25
    reps = max(1, int(seconds / chunk))
    for _ in range(reps):
        data = record_audio(chunk, samplerate=DEFAULT_SAMPLE_RATE)
        if data is None:
            break
        frames.append(compute_rms(data))
        time.sleep(0.05)
    if frames:
        if np is not None:
            NOISE_RMS = float(np.percentile(np.array(frames, dtype=np.float32), 20))
        else:
            frames_sorted = sorted(frames)
            idx = max(0, int(len(frames_sorted) * 0.2) - 1)
            NOISE_RMS = frames_sorted[idx] if frames_sorted else 0.0
    else:
        NOISE_RMS = 0.0
    log_action("vad_noise_calibrated", f"rms={NOISE_RMS:.6f}")

def rms_threshold():
    stt_cfg = CONFIG["stt"]
    mult = float(stt_cfg.get("vad_rms_multiplier", 0.0))
    if mult <= 0:
        return 0.0
    base = (NOISE_RMS or 0.0) * mult
    return max(float(stt_cfg.get("vad_min_threshold", 0.0)), float(base))

# -------------------------
# Voice enrollment & verification (simple waveform similarity)
# -------------------------
def enroll_voice_profile():
    try:
        say("Starting voice enrollment. Please speak the prompted phrases.")
        phrases = CONFIG.get("enroll_phrases", [])
        profile = {"samples": []}
        os.makedirs("voice_samples", exist_ok=True)
        for idx, p in enumerate(phrases):
            say("Please say: " + p)
            aud = record_audio(CONFIG.get("enroll_seconds", 4))
            if aud is None:
                resp = safe_input("Type a short text to record as placeholder: ").strip()
                profile["samples"].append({"phrase": p, "placeholder": resp})
                continue
            filename = f"voice_samples/sample_{idx}_{int(time.time())}.wav"
            save_wav(filename, aud, samplerate=DEFAULT_SAMPLE_RATE)
            profile["samples"].append({"phrase": p, "file": filename})
            time.sleep(0.3)
        with open(CONFIG["voice_profile_file"], "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        log_action("voice_enrolled")
        say("Voice enrollment complete.")
        return True
    except Exception as e:
        log_action("voice_enroll_error", str(e), "ERROR")
        return False

def load_profile_waveforms():
    if np is None:
        return []
    if not os.path.exists(CONFIG["voice_profile_file"]):
        return []
    try:
        with open(CONFIG["voice_profile_file"], "r", encoding="utf-8") as f:
            profile = json.load(f)
        waves = []
        for s in profile.get("samples", []):
            if "file" in s and os.path.exists(s["file"]):
                with wave.open(s["file"], "rb") as wf:
                    sr = wf.getframerate()
                    frames = wf.readframes(wf.getnframes())
                    arr = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32767.0
                    if sr != DEFAULT_SAMPLE_RATE and len(arr) > 0:
                        target_len = int(len(arr) * (DEFAULT_SAMPLE_RATE / float(sr)))
                        xp = np.linspace(0, 1, num=len(arr), endpoint=False, dtype=np.float32)
                        x_new = np.linspace(0, 1, num=target_len, endpoint=False, dtype=np.float32)
                        arr = np.interp(x_new, xp, arr).astype(np.float32)
                    waves.append(arr)
        return waves
    except Exception as e:
        log_action("voice_profile_load_error", str(e), "ERROR")
        return []

def waveform_cosine_similarity(a, b):
    if np is None:
        return 0.0
    try:
        a = np.asarray(a, dtype=np.float32)
        b = np.asarray(b, dtype=np.float32)
        la, lb = len(a), len(b)
        if la == 0 or lb == 0:
            return 0.0
        L = max(la, lb)
        if la < L:
            a = np.pad(a, (0, L - la))
        if lb < L:
            b = np.pad(b, (0, L - lb))
        a = a - np.mean(a)
        b = b - np.mean(b)
        na = np.linalg.norm(a)
        nb = np.linalg.norm(b)
        if na == 0 or nb == 0:
            return 0.0
        sim = float(np.dot(a, b) / (na * nb))
        return sim
    except Exception as e:
        log_action("waveform_similarity_error", str(e), "ERROR")
        return 0.0

def verify_voice_match(threshold: float = None) -> bool:
    if np is None:
        say("Voice verification requires numpy.")
        return False
    threshold = threshold if threshold is not None else CONFIG.get("voice_similarity_threshold", 0.5)
    enrolled = load_profile_waveforms()
    if not enrolled:
        say("No enrolled voice samples found.")
        return False
    sample = record_audio(CONFIG.get("verify_seconds", 4))
    if sample is None:
        say("Voice verification not available (no microphone).")
        return False
    sims = [waveform_cosine_similarity(sample, e) for e in enrolled]
    best = max(sims) if sims else 0.0
    log_action("voice_verify_similarity", str(best))
    return best >= threshold

# -------------------------
# Whisper model loading and STT
# -------------------------
def build_initial_prompt():
    stt = CONFIG.get("stt", {})
    if not stt.get("initial_prompt_enabled", False):
        return None
    custom = stt.get("initial_prompt_text")
    if custom:
        return custom
    apps = sorted(set(list(CONFIG["app_paths"].keys()) + list(CONFIG["app_aliases"].keys())))
    examples = [
        "jarvis open instagram",
        "jarvis open youtube",
        "jarvis open youtube music",
        "jarvis open spotify",
        "jarvis open youtube and play <song>",
        "jarvis play <song> on youtube",
        "jarvis search <query> on google",
        "jarvis scroll down",
        "jarvis page down",
        "jarvis play",
        "jarvis pause",
        "jarvis resume",
        "jarvis next",
        "jarvis previous",
        "jarvis mute",
        "jarvis volume up",
        "jarvis set volume to fifty percent",
        "jarvis set brightness to seventy percent",
        "jarvis seek forward ten seconds",
        "jarvis fullscreen",
        "jarvis exit fullscreen",
        "jarvis turn on wifi",
        "jarvis turn off wifi",
        "jarvis connect wifi <ssid>",
        "jarvis turn on bluetooth",
        "jarvis turn off bluetooth",
        "jarvis list bluetooth devices",
        "jarvis battery status",
        "jarvis shutdown computer",
        "jarvis restart computer",
        "jarvis lock computer",
    ]
    more = [f"jarvis open {a}" for a in apps if len(a) <= 20][:50]
    return "Commands are short and in English. Examples:\n" + "\n".join(examples + more)

def transcribe_options():
    stt = CONFIG["stt"]
    opts = dict(
        temperature=float(stt["temperature"]),
        condition_on_previous_text=bool(stt["condition_on_previous_text"]),
        no_speech_threshold=0.6,
        logprob_threshold=-1.0,
        compression_ratio_threshold=2.4,
        fp16=False,
        task="transcribe",
    )
    if stt.get("force_language", True) and stt.get("language"):
        opts["language"] = stt["language"]
    init = build_initial_prompt()
    if init:
        opts["initial_prompt"] = init
    return opts

def load_whisper_model():
    global WHISPER_MODEL
    if whisper is None:
        log_action("whisper_not_available", "openai-whisper not installed", "WARNING")
        WHISPER_MODEL = None
        return None
    model_path = CONFIG.get("whisper_model_path") or None
    fallback_model = CONFIG["stt"].get("fallback_model", "small.en")
    if model_path:
        try:
            WHISPER_MODEL = whisper.load_model(model_path)
            print(f"✓ Whisper model loaded: {model_path}")
            log_action("whisper_model_loaded", str(model_path))
            return WHISPER_MODEL
        except Exception as e:
            log_action("whisper_model_load_error_primary", f"{model_path} -> {str(e)}", "ERROR")
    try:
        WHISPER_MODEL = whisper.load_model(fallback_model)
        print(f"✓ Whisper model loaded: {fallback_model} (fallback)")
        log_action("whisper_model_loaded_fallback", fallback_model)
        return WHISPER_MODEL
    except Exception as e2:
        log_action("whisper_model_load_error_fallback", str(e2), "ERROR")
        try:
            WHISPER_MODEL = whisper.load_model("small")
            print("✓ Whisper model loaded: small (secondary fallback)")
            log_action("whisper_model_loaded_secondary_fallback", "small")
            return WHISPER_MODEL
        except Exception as e3:
            log_action("whisper_model_load_error_secondary_fallback", str(e3), "ERROR")
            WHISPER_MODEL = None
            print("✗ Failed to load any Whisper model. Using console input for STT.")
            return None

def capture_short_audio_command(seconds: int = 3) -> str:
    model = globals().get("WHISPER_MODEL", None)
    stt_cfg = CONFIG["stt"]
    debug = bool(stt_cfg.get("debug", False))
    if sd is not None and model is not None:
        try:
            data = record_audio(seconds, samplerate=DEFAULT_SAMPLE_RATE)
            if data is not None:
                rms = compute_rms(data)
                thr = rms_threshold()
                if debug:
                    print(f"[STT DEBUG] RMS={rms:.6f} Thr={thr:.6f} Gate={'OFF' if thr<=0 else ('PASS' if rms>=thr else 'SKIP')}")
                if thr > 0 and rms < thr:
                    return ""
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tf:
                    tmp_wav = tf.name
                save_wav(tmp_wav, data, samplerate=DEFAULT_SAMPLE_RATE)
                try:
                    result = model.transcribe(tmp_wav, **transcribe_options())
                    text = (result.get("text") or "").strip()
                finally:
                    try:
                        os.remove(tmp_wav)
                    except Exception:
                        pass
                return text
        except Exception as e:
            log_action("capture_short_audio_error", str(e), "ERROR")
    t = safe_input("> ")
    return (t or "").strip()

def stt_generator():
    stt_cfg = CONFIG["stt"]
    model = globals().get("WHISPER_MODEL", None)
    if sd is not None and model is not None:
        if stt_cfg.get("vad_auto_calibrate", False):
            calibrate_noise(float(stt_cfg.get("vad_calibration_seconds", 1.0)))

        samplerate = DEFAULT_SAMPLE_RATE
        chunk_seconds = int(stt_cfg.get("chunk_seconds", 4)) or 4
        debug = bool(stt_cfg.get("debug", False))

        while True:
            try:
                print("[STT] Listening...")
                data = record_audio(chunk_seconds, samplerate=samplerate)
                if data is None:
                    print("[STT] Microphone not available. Falling back to console.")
                    break

                rms = compute_rms(data)
                thr = rms_threshold()
                if debug:
                    print(f"[STT DEBUG] RMS={rms:.6f} Thr={thr:.6f} Gate={'OFF' if thr<=0 else ('PASS' if rms>=thr else 'SKIP')}")

                if thr > 0 and rms < thr:
                    continue

                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tf:
                    tmp_wav = tf.name
                save_wav(tmp_wav, data, samplerate=samplerate)
                try:
                    result = model.transcribe(tmp_wav, **transcribe_options())
                    text = (result.get("text") or "").strip()
                finally:
                    try:
                        os.remove(tmp_wav)
                    except Exception:
                        pass

                if text:
                    yield text
                else:
                    continue
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log_action("stt_error", str(e), "ERROR")
                print(f"[STT Error] {str(e)}")
                break

    say("[STT] Microphone/model unavailable. Using console input.")
    while True:
        try:
            t = safe_input("> ")
            if t is None:
                time.sleep(0.1)
                continue
            t = t.strip()
            if not t:
                continue
            yield t
        except KeyboardInterrupt:
            raise
        except Exception:
            time.sleep(0.1)

# -------------------------
# Confirmation helper
# -------------------------
def confirm_action(prompt: str, require_auth: bool = False) -> bool:
    say(prompt + " Please confirm by saying 'yes' or typing yes.")
    if require_auth:
        try:
            ok = verify_voice_match()
        except Exception:
            ok = False
        if ok:
            return True
        say("Voice verification failed or unavailable. Type 'yes' to confirm.")
    resp = safe_input("> ").strip().lower()
    return resp in ("y", "yes")

# -------------------------
# Wake word helpers (with fuzzy)
# -------------------------
def similar(a: str, b: str) -> float:
    try:
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()
    except Exception:
        return 0.0

def has_wake_word(text: str) -> bool:
    ww = CONFIG.get("wake_word", "jarvis").lower()
    text_l = (text or "").lower()
    if CONFIG.get("wake_word_anywhere", False):
        if ww in text_l:
            return True
        if CONFIG.get("wake_word_fuzzy", True):
            words = re.findall(r"\w+", text_l)
            for w in words:
                if similar(w, ww) >= float(CONFIG.get("wake_word_fuzzy_threshold", 0.8)):
                    return True
        return False
    else:
        if text_l.startswith(ww):
            return True
        if CONFIG.get("wake_word_fuzzy", True):
            first = re.findall(r"^\w+", text_l)
            if first:
                if similar(first[0], ww) >= float(CONFIG.get("wake_word_fuzzy_threshold", 0.8)):
                    return True
        return False

def strip_wake_word(text: str) -> str:
    ww = CONFIG.get("wake_word", "jarvis").lower()
    t = (text or "").lower().strip()
    if CONFIG.get("wake_word_anywhere", False):
        idx = t.find(ww)
        if idx >= 0:
            t = (t[:idx] + t[idx + len(ww):]).strip()
            return t
        if CONFIG.get("wake_word_fuzzy", True):
            tokens = t.split()
            for i, tok in enumerate(tokens):
                if similar(tok, ww) >= float(CONFIG.get("wake_word_fuzzy_threshold", 0.8)):
                    del tokens[i]
                    return " ".join(tokens).strip()
        return t
    else:
        if t.startswith(ww):
            return t[len(ww):].strip()
        if CONFIG.get("wake_word_fuzzy", True):
            parts = t.split(maxsplit=1)
            if parts and similar(parts[0], ww) >= float(CONFIG.get("wake_word_fuzzy_threshold", 0.8)):
                return parts[1] if len(parts) > 1 else ""
        return t


def parse_whatsapp_command(text: str):
    """
    Name-first WhatsApp parser. Supports:
      - 'send message to mom I will be late'  (with or without 'on whatsapp')
      - 'send whatsapp to mom I will be late'
      - 'message john on whatsapp hello'
      - 'whatsapp sidhant I am on my way'
      - 'open whatsapp send message to mom I will be late'
    Returns:
      {"action":"whatsapp_send","contact":"<name>","message":"..."} OR
      {"action":"whatsapp_send","target":"<digits>","message":"..."}
    """
    raw = text or ""
    t = raw.lower().strip().replace("whats app", "whatsapp")

    # Strip a leading 'open whatsapp' so we still parse the instruction
    m_open = re.match(r'^(open|launch|go to|navigate to)\s+whatsapp\b\s*(.*)$', t)
    if m_open:
        t = m_open.group(2).strip()

    # 1) send message to <name> [on whatsapp] <message>?
    m1 = re.search(r'\bsend\s+(?:a\s+)?message\s+to\s+(.+)$', t)
    if m1:
        tail = m1.group(1).strip()
        tail = re.sub(r'\s+on\s+whatsapp\b', '', tail).strip()
        # Prefer contact name
        name, msg = split_contact_and_message(tail)
        if name:
            return {"action": "whatsapp_send", "contact": name.strip(), "message": (msg or "").strip()}
        # Fallback to explicit number
        mnum = re.match(r'(\+?[\d\s\-()]{8,})\s*(.*)$', tail)
        if mnum:
            return {"action":"whatsapp_send", "target": mnum.group(1).strip(), "message": (mnum.group(2) or "").strip()}
        return None

    # 2) send whatsapp to <target> <message>
    m2 = re.search(r'\bsend\s+whatsapp\s+to\s+(.+)$', t)
    if m2:
        tail = m2.group(1).strip()
        name, msg = split_contact_and_message(tail)
        if name and msg:
            return {"action":"whatsapp_send", "contact": name.strip(), "message": msg.strip()}
        mnum = re.match(r'(\+?[\d\s\-()]{8,})\s+(.+)', tail)
        if mnum:
            return {"action":"whatsapp_send", "target": mnum.group(1).strip(), "message": mnum.group(2).strip()}
        if name:
            return {"action":"whatsapp_send", "contact": name.strip(), "message": (msg or "").strip()}
        return None

    # 3) message <name> [on whatsapp] <message>?
    m3 = re.search(r'\bmessage\s+(.+?)(?:\s+on\s+whatsapp)?\s*(.*)$', t)
    if m3 and "whatsapp" in t:  # ensure it’s about WhatsApp for this pattern
        return {"action":"whatsapp_send", "contact": m3.group(1).strip(), "message": (m3.group(2) or "").strip()}

    # 4) whatsapp <tail>
    m4 = re.search(r'\bwhatsapp\s+(.+)$', t)
    if m4:
        tail = m4.group(1).strip()
        name, msg = split_contact_and_message(tail)
        if name and msg:
            return {"action":"whatsapp_send", "contact": name.strip(), "message": msg.strip()}
        mnum = re.match(r'(\+?[\d\s\-()]{8,})\s+(.+)', tail)
        if mnum:
            return {"action":"whatsapp_send", "target": mnum.group(1).strip(), "message": mnum.group(2).strip()}
        if name:
            return {"action":"whatsapp_send", "contact": name.strip(), "message": (msg or "").strip()}

    return None

def parse_window_command(text: str):
    """
    Parse window management commands:
    - minimize <app>, maximize <app>, restore <app>, focus/switch to <app>
    - minimize this window, minimize all windows
    Returns dict: {action, app_key|None, scope} or None
    """
    t = (text or "").lower().strip()

    # Minimize all / show desktop
    if re.search(r'\b(minimi[sz]e\s+all|show\s+desktop)\b', t):
        return {"action": "minimize_all"}

    # Minimize/Maximize/Restore/Focus active window
    if re.search(r'\b(minimi[sz]e|maximi[sz]e|restore)\s+(this\s+)?(window|tab)\b', t):
        act = "minimize" if "minimi" in t else "maximize" if "maximi" in t else "restore"
        return {"action": act, "app_key": None, "scope": "active"}

    # Focus/switch to this window (treat as focus active - no-op)
    if re.search(r'\b(focus|bring|switch)\s+(this\s+)?(window|tab)\b', t):
        return {"action": "focus", "app_key": None, "scope": "active"}

    # Specific app by name
    m = re.match(r'^(minimi[sz]e|maximi[sz]e|restore|focus|bring|switch\s+to)\s+(.+)$', t)
    if m:
        action_raw = m.group(1)
        name = m.group(2).strip()
        action = "focus" if action_raw in ("focus", "bring") or action_raw.startswith("switch") else \
                 "minimize" if action_raw.startswith("minimi") else \
                 "maximize" if action_raw.startswith("maximi") else "restore"
        app_key = canonical_app_key(name) or name.lower()
        return {"action": action, "app_key": app_key, "scope": "app"}

    # Simple "minimize <app>" shortcut
    m2 = re.match(r'^(minimi[sz]e)\s+(.+)$', t)
    if m2:
        app_key = canonical_app_key(m2.group(2).strip()) or m2.group(2).strip().lower()
        return {"action": "minimize", "app_key": app_key, "scope": "app"}

    return None





# -------------------------
# Voice command handler (patched order)
# -------------------------
def handle_voice_command(command_text: str):
    if not command_text:
        return
    if re.fullmatch(r'[.\s…]+', command_text):
        return

    original_text = command_text
    lc_all = command_text.lower().strip()

    # Allow certain commands without wake word
    allow_without_wake = False
    # Open synonyms allowed without wake word
    if CONFIG.get("allow_open_without_wake", True) and re.match(r'^(open|launch|start|turn on|go to|navigate to)\s+', lc_all):
        allow_without_wake = True
    # Controls without wake word (media/scroll/volume/brightness)

    if CONFIG.get("allow_controls_without_wake", True):
    	if (
	   parse_media_control(lc_all)
        or parse_scroll_command(lc_all)
        or parse_volume_command(lc_all)
        or parse_brightness_command(lc_all)
        or parse_whatsapp_command(lc_all)
        or parse_instagram_command(lc_all)):
        or parse_window_command(lc_all)): 
        allow_without_wake = True
    if (parse_play_command(lc_all) or parse_search_command(lc_all) or parse_whatsapp_command(lc_all)):
    	allow_without_wake = True
    # Play/search intents without wake (so "play ... on spotify" works)
    if parse_play_command(lc_all) or parse_search_command(lc_all):
        allow_without_wake = True
    # Close/exit/quit without wake word
    if re.match(r'^(close|exit|quit)\s+', lc_all):
        allow_without_wake = True
    # "close tab" without wake word
    if re.fullmatch(r'\s*(close|exit|quit)\s+(this\s+)?tab\s*', lc_all):
        allow_without_wake = True

    # Wake word enforcement
    if CONFIG.get("require_wake_word", True) and not allow_without_wake:
        if has_wake_word(command_text):
            command_text = strip_wake_word(command_text)
        else:
            print(f"[WakeWord] Ignored (no wake word): {original_text}")
            return

    lc = command_text.lower().strip()

    # Close tab (no app specified)
    if re.fullmatch(r'\s*(close|exit|quit)\s+(this\s+)?tab\s*', lc):
        press_combo(VK_CONTROL, ord('W'))  # Ctrl+W
        say("Closed tab.")
        return

    # Close app by name
    m_close = re.match(r'^(close|exit|quit)\s+(.+)$', lc)
    if m_close:
        close_app(m_close.group(2))
        return

    # 1) Volume control
    vol_cmd = parse_volume_command(lc)
    if vol_cmd:
        if vol_cmd["action"] == "volume_set":
            volume_set_percent(int(vol_cmd["value"]))
        elif vol_cmd["action"] == "volume_change":
            volume_change_percent(int(vol_cmd["delta"]))
        return

    # 2) Brightness control
    bri_cmd = parse_brightness_command(lc)
    if bri_cmd:
        if bri_cmd["action"] == "brightness_set":
            brightness_set_percent(int(bri_cmd["value"]))
        elif bri_cmd["action"] == "brightness_change":
            brightness_change_percent(int(bri_cmd["delta"]))
        return
     
    # 2.5) WhatsApp send
    whats_cmd = parse_whatsapp_command(lc)
    if whats_cmd:
        if handle_whatsapp_command(whats_cmd):
            return
    # 2.6) Instagram actions
    ig_cmd = parse_instagram_command(lc)
    if ig_cmd:
        if handle_instagram_command(ig_cmd):
            return
    
    # 2.7) Window management (minimize/maximize/restore/focus)
    win_cmd = parse_window_command(lc)
    if win_cmd:
        if handle_window_command(win_cmd):
            return	



    # 3) Open app (with synonyms) — moved BEFORE intents
    if re.match(r'^(open|launch|start|turn on|go to|navigate to)\s+', lc):
        phrase = re.sub(r'^(open|launch|start|turn on|go to|navigate to)\s+', 'open ', command_text, flags=re.I)
        handle_open_phrase(phrase)
        return

    # 4) Intents (play/search/follow-ups) BEFORE generic media controls
    if handle_followup_or_intents(command_text):
        return

    # 5) Media controls
    media_cmd = parse_media_control(lc)
    if media_cmd and handle_media_control(media_cmd):
        return

    # 6) Scroll controls
    scroll_cmd = parse_scroll_command(lc)
    if scroll_cmd and handle_scroll(scroll_cmd):
        return

    # 7) Wi‑Fi and Bluetooth
    if lc.startswith(("turn on wifi", "enable wifi", "wifi on")):
        wifi_enable()
        return
    if lc.startswith(("turn off wifi", "disable wifi", "wifi off")):
        wifi_disable()
        return
    if lc.startswith("connect wifi"):
        ssid = lc.replace("connect wifi", "", 1).strip()
        if ssid:
            wifi_connect(ssid)
        else:
            say("Please specify the Wi‑Fi network name.")
        return
    if lc.startswith(("disconnect wifi", "wifi disconnect")):
        wifi_disconnect()
        return

    if lc.startswith(("turn on bluetooth", "enable bluetooth", "bluetooth on")):
        bluetooth_enable()
        return
    if lc.startswith(("turn off bluetooth", "disable bluetooth", "bluetooth off")):
        bluetooth_disable()
        return
    if lc.startswith(("list bluetooth devices", "bluetooth devices")):
        devices = list_bluetooth_devices()
        if devices:
            say(f"Found {len(devices)} Bluetooth devices:")
            for d in devices:
                say(d.get("name", "Unknown device"))
        else:
            say("No Bluetooth devices found.")
        return

    # Battery
    if lc.startswith(("battery status", "battery")):
        battery_status()
        return

    # System
    if lc.startswith(("shutdown computer", "shutdown")):
        if confirm_action("Shutdown the computer?", require_auth=True):
            say("Shutting down the computer.")
            if os.name == 'nt':
                os.system("shutdown /s /t 5")
            else:
                os.system("shutdown -h now")
        return

    if lc.startswith(("restart computer", "restart")):
        if confirm_action("Restart the computer?", require_auth=True):
            say("Restarting the computer.")
            if os.name == 'nt':
                os.system("shutdown /r /t 5")
            else:
                os.system("reboot")
        return

    if lc.startswith(("lock computer", "lock")):
        say("Locking the computer.")
        if os.name == 'nt':
            os.system("rundll32.exe user32.dll,LockWorkStation")
        else:
            try:
                subprocess.run(["gnome-screensaver-command", "-l"])
            except Exception:
                log_action("lock_command_error", "Failed to lock non-Windows system", "WARNING")
        return

    if lc in ["help", "jarvis help"]:
        say(
            "You can say: open/go to <app>, open <app> and play <song>, play <song> on <app>, "
            "search <query> on <app>, close <app>, close tab, scroll up/down or page up/down or scroll to top/bottom, "
            "set volume to fifty percent, increase volume by ten, set brightness to seventy percent, "
            "play, pause, resume, next, previous, mute, unmute, volume up/down, seek forward ten seconds, "
            "fullscreen, exit fullscreen, turn on wifi, connect wifi <name>, turn on bluetooth, battery status, "
            "shutdown computer, restart computer, lock computer."
        )
        return

    say("Sorry, I didn't understand that command.")
    log_action("command_unknown", command_text)

# -------------------------
# Main Entry Point
# -------------------------
if __name__ == "__main__":
    print("\n" + "="*60)
    print("JARVIS Voice Assistant - Hardened Security Version")
    print("="*60 + "\n")

    print(f"[DEBUG] Script: {os.path.abspath(__file__)}")
    print(f"[DEBUG] Trusted: {os.path.abspath(CONFIG['trusted_folder'])}")

    # Initialize TTS
    tts_engine = setup_tts()

    # Security checks
    print("[Security Check] Verifying trusted folder...")
    if not is_running_from_trusted_folder():
        say("Warning: The script is not running from the trusted folder. Administrative actions are blocked.")
        print("⚠️  NOT in trusted folder - Admin actions may be disabled")
    else:
        say("Script running from trusted folder.")
        print("✓ Running from trusted folder")

    print("[Security Check] Checking administrator privileges...")
    if not is_admin():
        say("Running with limited permissions. For full admin operations, run as Administrator.")
        print("⚠️  Limited permissions - Some features unavailable")
    else:
        print("✓ Running with Administrator privileges")

    # Load Whisper model
    load_whisper_model()

    # Voice enrollment check
    print("\n[Voice Authentication] Checking for voice profile...")
    if not os.path.exists(CONFIG["voice_profile_file"]):
        print("⚠️  No voice profile found")
        say("No voice profile found. Do you want to enroll your voice now?")
        say("Say yes to enroll, or type yes.")
        resp = capture_short_audio_command(3)
        do_enroll = False
        if resp and re.search(r"\b(yes|enroll)\b", resp, re.I):
            do_enroll = True
        else:
            ans = safe_input("Enroll now? Type 'yes' to enroll: ").strip().lower()
            if ans == "yes":
                do_enroll = True
        if do_enroll:
            ok = enroll_voice_profile()
            if not ok:
                say("Enrollment failed. You can enroll later by deleting the voice profile file and rerunning.")
                print("✗ Enrollment failed")
            else:
                print("✓ Voice profile enrolled successfully")
    else:
        print("✓ Voice profile found")

    # Start listening
    print("\n" + "="*60)
    print("JARVIS is now online and listening...")
    print(f"Wake word: '{CONFIG['wake_word']}' (required: {CONFIG.get('require_wake_word', True)})")
    print("Say 'jarvis help' for available commands, or 'open <app>' without wake word.")
    print("Press Ctrl+C to exit")
    print("="*60 + "\n")

    say("Jarvis is online and listening.")
    log_action("jarvis_started")

    try:
        for phrase in stt_generator():
            print(f"\n[You said]: {phrase}")
            try:
                handle_voice_command(phrase)
            except Exception as e:
                print(f"[Command Error] {str(e)}")
                log_action("command_error", str(e), "ERROR")
            time.sleep(0.2)

    except KeyboardInterrupt:
        print("\n\n[Shutdown] Stopping JARVIS...")
        say("Goodbye.")
        log_action("jarvis_stopped")

    except Exception as e:
        print(f"\n[FATAL ERROR] {str(e)}")
        log_action("jarvis_fatal_error", str(e), "CRITICAL")
        try:
            say("I encountered a critical error and need to stop.")
        except Exception:
            pass

    finally:
        shutdown_tts()
        print("JARVIS shutdown complete.\n")