#!/usr/bin/env python3
"""
Auto-configure ComfyUI API key from environment variable.

This script checks for COMFYUI_API_KEY environment variable and pre-populates
the user settings file so that API nodes can authenticate automatically without
requiring manual sign-in through the UI.

Usage:
    python scripts/configure_api_key.py

Environment Variables:
    COMFYUI_API_KEY - The API key to configure for api.comfy.org authentication
"""

import os
import sys
import json
import logging

# Add comfy to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import folder_paths
from comfy.cli_args import args

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def configure_api_key():
    """Configure API key from environment variable."""
    api_key = os.environ.get("COMFYUI_API_KEY")

    if not api_key:
        logger.info("No COMFYUI_API_KEY environment variable found. Skipping API key configuration.")
        return False

    # Get user directory
    user_directory = folder_paths.get_user_directory()

    # Determine user (default or multi-user)
    if args.multi_user:
        logger.warning("Multi-user mode detected. API key auto-configuration only works for default user.")
        user = "default"
    else:
        user = "default"

    # Create user directory if it doesn't exist
    user_path = os.path.join(user_directory, user)
    if not os.path.exists(user_path):
        os.makedirs(user_path, exist_ok=True)
        logger.info(f"Created user directory: {user_path}")

    # Path to settings file
    settings_file = os.path.join(user_path, "comfy.settings.json")

    # Load existing settings or create new
    settings = {}
    if os.path.exists(settings_file):
        try:
            with open(settings_file, 'r') as f:
                settings = json.load(f)
            logger.info(f"Loaded existing settings from {settings_file}")
        except json.JSONDecodeError:
            logger.warning(f"Settings file {settings_file} is corrupted. Creating new settings.")
            settings = {}

    # Update API key
    settings["api_key_comfy_org"] = api_key

    # Save settings
    with open(settings_file, 'w') as f:
        json.dump(settings, f, indent=4)

    # Mask the key for logging (show first 16 chars only)
    masked_key = api_key[:16] + "*" * (len(api_key) - 16) if len(api_key) > 16 else api_key[:8] + "***"
    logger.info(f"✅ Successfully configured ComfyUI API key: {masked_key}")
    logger.info(f"   Settings saved to: {settings_file}")

    return True


if __name__ == "__main__":
    configure_api_key()
