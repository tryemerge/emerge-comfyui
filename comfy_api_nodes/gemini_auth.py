"""
Gemini Authentication Abstraction Layer

Supports both Google AI Studio and Vertex AI backends.
Auto-detects which to use based on environment variables:

- GEMINI_API_KEY → Google AI Studio (simple API key)
- GOOGLE_APPLICATION_CREDENTIALS → Vertex AI (GCP service account)
"""

import os
from typing import Optional
from comfy_api_nodes.util import ApiEndpoint


# Environment variable detection
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID')
GCP_REGION = os.environ.get('GCP_REGION', 'us-central1')
GOOGLE_APPLICATION_CREDENTIALS = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')


def is_vertex_ai_configured() -> bool:
    """
    Check if Vertex AI credentials are configured.

    Returns True if:
    - GCP_PROJECT_ID is set AND
    - Either GOOGLE_APPLICATION_CREDENTIALS is set OR gcloud auth is configured
    """
    if not GCP_PROJECT_ID:
        return False

    # If explicit credentials file is set, we're good
    if GOOGLE_APPLICATION_CREDENTIALS:
        return True

    # Check if gcloud Application Default Credentials are available
    try:
        import google.auth
        credentials, project = google.auth.default()
        return True
    except Exception:
        return False


def is_ai_studio_configured() -> bool:
    """Check if Google AI Studio API key is configured."""
    return bool(GEMINI_API_KEY)


def get_auth_backend() -> str:
    """
    Determine which authentication backend to use.

    Returns:
        "vertex_ai" or "ai_studio"

    Raises:
        ValueError: If neither backend is properly configured
    """
    if is_vertex_ai_configured():
        return "vertex_ai"
    elif is_ai_studio_configured():
        return "ai_studio"
    else:
        raise ValueError(
            "No Gemini authentication configured. Please set either:\n"
            "  1. GEMINI_API_KEY for Google AI Studio\n"
            "  2. GOOGLE_APPLICATION_CREDENTIALS + GCP_PROJECT_ID for Vertex AI\n"
            "\n"
            "Get API key at: https://aistudio.google.com/apikey\n"
            "Or setup GCP service account: https://cloud.google.com/vertex-ai/docs/authentication"
        )


def get_vertex_ai_access_token() -> str:
    """
    Get OAuth2 access token for Vertex AI using available credentials.

    Supports multiple authentication methods:
    1. Service account key file (if GOOGLE_APPLICATION_CREDENTIALS is set)
    2. Workload Identity Federation config file
    3. Application Default Credentials (gcloud auth application-default login)
    4. Compute Engine/GKE metadata service (when running on GCP)

    Returns:
        Bearer token string
    """
    try:
        from google.auth.transport.requests import Request
        import google.auth
    except ImportError:
        raise ImportError(
            "google-auth library not installed. Install it with:\n"
            "pip install google-auth"
        )

    # Use Application Default Credentials (ADC) - automatically handles:
    # - Service account keys
    # - Workload Identity Federation
    # - gcloud user credentials
    # - GCE/GKE metadata service
    credentials, project = google.auth.default(
        scopes=['https://www.googleapis.com/auth/cloud-platform']
    )

    # Refresh token if needed
    if not credentials.valid:
        credentials.refresh(Request())

    return credentials.token


def get_gemini_endpoint(model: str, action: str = "generateContent") -> ApiEndpoint:
    """
    Create an ApiEndpoint configured for the appropriate backend.

    Args:
        model: Model name (e.g., "gemini-3-pro-preview")
        action: API action (e.g., "generateContent", "streamGenerateContent")

    Returns:
        Configured ApiEndpoint with authentication
    """
    backend = get_auth_backend()

    if backend == "vertex_ai":
        # Vertex AI endpoint format:
        # https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}/publishers/google/models/{model}:{action}
        path = (
            f"https://{GCP_REGION}-aiplatform.googleapis.com/v1/"
            f"projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/"
            f"publishers/google/models/{model}:{action}"
        )
        endpoint = ApiEndpoint(path=path, method="POST")

        # Get OAuth2 token for Vertex AI
        access_token = get_vertex_ai_access_token()
        endpoint.headers = {"Authorization": f"Bearer {access_token}"}

        print(f"[EmProps Gemini] Using Vertex AI backend (project={GCP_PROJECT_ID}, region={GCP_REGION})")

    else:  # ai_studio
        # Google AI Studio endpoint format:
        # https://generativelanguage.googleapis.com/v1beta/models/{model}:{action}
        path = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:{action}"
        endpoint = ApiEndpoint(path=path, method="POST")
        endpoint.query_params = {"key": GEMINI_API_KEY}

        print(f"[EmProps Gemini] Using AI Studio backend")

    return endpoint


def get_auth_info() -> dict:
    """
    Get information about current authentication configuration.

    Returns:
        Dict with backend info for debugging
    """
    try:
        backend = get_auth_backend()

        if backend == "vertex_ai":
            return {
                "backend": "vertex_ai",
                "project_id": GCP_PROJECT_ID,
                "region": GCP_REGION,
                "credentials_file": GOOGLE_APPLICATION_CREDENTIALS,
            }
        else:
            return {
                "backend": "ai_studio",
                "api_key_set": bool(GEMINI_API_KEY),
            }
    except ValueError as e:
        return {
            "backend": "none",
            "error": str(e),
        }
