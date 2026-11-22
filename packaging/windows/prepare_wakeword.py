"""Prepare the pretrained wake-word assets before PyInstaller freezes PAX."""
from openwakeword.utils import download_models

try:
    download_models(["hey_jarvis"])
except TypeError:
    download_models()
print("Wake-word model assets prepared.")
