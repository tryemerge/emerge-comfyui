"""
Enhanced Error Handling for Gemini API Nodes

This module provides comprehensive error handling, logging, and debugging
for Gemini API calls to make failures crystal clear and debuggable.
"""

import traceback
import json
from typing import Any, Optional
from datetime import datetime
from pydantic import BaseModel


def _make_serializable(obj: Any) -> Any:
    """
    Recursively convert objects to JSON-serializable format

    Args:
        obj: Object to convert

    Returns:
        JSON-serializable version of the object
    """
    if obj is None:
        return None
    elif isinstance(obj, (str, int, float, bool)):
        return obj
    elif isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_make_serializable(item) for item in obj]
    elif hasattr(obj, 'model_dump'):
        # Pydantic model
        try:
            return obj.model_dump(mode='json')
        except Exception:
            return str(obj)
    elif hasattr(obj, 'dict'):
        # Older Pydantic model
        try:
            return obj.dict()
        except Exception:
            return str(obj)
    elif hasattr(obj, '__dict__'):
        return _make_serializable(obj.__dict__)
    else:
        return str(obj)


class GeminiErrorDetails(BaseModel):
    """Structured error information for debugging"""
    timestamp: str
    error_type: str
    error_message: str
    gemini_response: Optional[dict] = None
    request_details: Optional[dict] = None
    full_traceback: str
    suggestions: list[str]

    def to_readable_string(self) -> str:
        """Convert error details to human-readable format"""
        lines = [
            "=" * 80,
            "🚨 GEMINI NODE EXECUTION FAILED",
            "=" * 80,
            f"Timestamp: {self.timestamp}",
            f"Error Type: {self.error_type}",
            "",
            "ERROR MESSAGE:",
            self.error_message,
            "",
        ]

        if self.gemini_response:
            lines.extend([
                "GEMINI API RESPONSE:",
                json.dumps(self.gemini_response, indent=2),
                "",
            ])

        if self.request_details:
            lines.extend([
                "REQUEST DETAILS:",
                json.dumps(self.request_details, indent=2),
                "",
            ])

        lines.extend([
            "SUGGESTED FIXES:",
        ])
        for i, suggestion in enumerate(self.suggestions, 1):
            lines.append(f"  {i}. {suggestion}")

        lines.extend([
            "",
            "FULL TRACEBACK:",
            self.full_traceback,
            "=" * 80,
        ])

        return "\n".join(lines)


def create_error_details(
    error: Exception,
    response: Any = None,
    request_data: dict = None,
) -> GeminiErrorDetails:
    """
    Create detailed error information from an exception

    Args:
        error: The exception that occurred
        response: The API response object (if available)
        request_data: Request parameters for debugging

    Returns:
        GeminiErrorDetails with comprehensive debugging info
    """
    error_type = type(error).__name__
    error_message = str(error)

    # Extract response data if available
    gemini_response = None
    if response:
        try:
            # Try Pydantic's model_dump first (handles nested models properly)
            if hasattr(response, 'model_dump'):
                gemini_response = response.model_dump(mode='json')
            elif hasattr(response, 'dict'):
                # Fallback for older Pydantic versions
                gemini_response = response.dict()
            elif hasattr(response, '__dict__'):
                # Last resort: convert to dict and stringify non-serializable objects
                gemini_response = _make_serializable(response.__dict__)
            else:
                gemini_response = {"raw": str(response)}
        except Exception as e:
            gemini_response = {"extraction_error": str(e), "response_type": type(response).__name__}

    # Generate suggestions based on error patterns
    suggestions = generate_suggestions(error_type, error_message, gemini_response)

    return GeminiErrorDetails(
        timestamp=datetime.utcnow().isoformat(),
        error_type=error_type,
        error_message=error_message,
        gemini_response=gemini_response,
        request_details=sanitize_request_data(request_data) if request_data else None,
        full_traceback=traceback.format_exc(),
        suggestions=suggestions,
    )


def sanitize_request_data(request_data: dict) -> dict:
    """Remove sensitive data from request details"""
    sanitized = request_data.copy()

    # Remove API keys
    if 'gemini_api_key' in sanitized:
        sanitized['gemini_api_key'] = "***REDACTED***"
    if 'api_key' in sanitized:
        sanitized['api_key'] = "***REDACTED***"

    # Truncate large base64 data
    if 'parts' in sanitized:
        parts = sanitized['parts']
        if isinstance(parts, list):
            sanitized_parts = []
            for part in parts:
                if isinstance(part, dict):
                    part_copy = part.copy()
                    if 'inlineData' in part_copy and isinstance(part_copy['inlineData'], dict):
                        if 'data' in part_copy['inlineData']:
                            data_len = len(part_copy['inlineData']['data'])
                            if data_len > 100:
                                part_copy['inlineData']['data'] = f"<base64 data: {data_len} bytes>"
                    sanitized_parts.append(part_copy)
                else:
                    sanitized_parts.append(str(part))
            sanitized['parts'] = sanitized_parts

    return sanitized


def generate_suggestions(
    error_type: str,
    error_message: str,
    response: Optional[dict],
) -> list[str]:
    """
    Generate actionable suggestions based on error patterns

    Args:
        error_type: Type of exception
        error_message: Error message text
        response: API response (if available)

    Returns:
        List of suggested fixes
    """
    suggestions = []

    # No candidates in response
    if "candidates is None" in error_message or "empty candidates" in error_message:
        suggestions.extend([
            "Check if your content violates Google's safety policies (harassment, hate speech, sexually explicit, dangerous content)",
            "Verify your GEMINI_API_KEY is valid and has not exceeded quota",
            "Try reducing input size (large images/videos may be rejected)",
            "Check Gemini API status: https://status.cloud.google.com/",
            "Review the full Gemini response above for 'promptFeedback' or 'safetyRatings' fields",
        ])

    # API key issues
    if "API_KEY" in error_message or "authentication" in error_message.lower():
        suggestions.extend([
            "Verify GEMINI_API_KEY environment variable is set correctly",
            "Get a new API key from: https://aistudio.google.com/apikey",
            "Check if your API key has been revoked or expired",
            "Ensure the API key has Gemini API enabled in Google Cloud Console",
        ])

    # Rate limiting
    if "quota" in error_message.lower() or "rate" in error_message.lower() or "429" in error_message:
        suggestions.extend([
            "You've exceeded Gemini API rate limits - wait before retrying",
            "Check your quota in Google Cloud Console",
            "Consider upgrading to a paid plan for higher limits",
            "Implement exponential backoff for retries",
        ])

    # Network/timeout issues
    if "timeout" in error_message.lower() or "connection" in error_message.lower():
        suggestions.extend([
            "Check your internet connection",
            "Verify firewall isn't blocking requests to generativelanguage.googleapis.com",
            "Try again - this may be a temporary network issue",
            "Reduce input size (large files may timeout)",
        ])

    # Model not found
    if "model" in error_message.lower() and ("not found" in error_message.lower() or "404" in error_message):
        suggestions.extend([
            "Verify the model name is correct (e.g., 'gemini-2.5-pro', 'gemini-2.5-flash')",
            "Check if the model is available in your region",
            "Some preview models may have limited availability",
        ])

    # Invalid request
    if "invalid" in error_message.lower() or "400" in error_message:
        suggestions.extend([
            "Check the request format matches Gemini API spec",
            "Verify all required fields are present",
            "Check for unsupported MIME types in images/videos/audio",
            "Ensure prompt text is not empty",
        ])

    # Response parsing issues
    if response and "candidates" not in str(response):
        suggestions.extend([
            "Gemini API returned an unexpected response structure",
            "This may indicate an API version mismatch",
            "Check if Google has updated the Gemini API schema",
        ])

    # Generic fallback suggestions
    if not suggestions:
        suggestions.extend([
            "Enable debug logging to see full request/response details",
            "Check ComfyUI console for additional error messages",
            "Try with a simpler prompt to isolate the issue",
            "Verify all input nodes are providing valid data",
        ])

    return suggestions


def log_error_details(error_details: GeminiErrorDetails, node_name: str = "GeminiNode"):
    """
    Log error details to console with clear formatting

    Args:
        error_details: The error details to log
        node_name: Name of the node for context
    """
    header = f"[EmProps {node_name}] ERROR"
    print(f"\n{header}")
    print(error_details.to_readable_string())


def create_user_friendly_error_message(error_details: GeminiErrorDetails) -> str:
    """
    Create a concise error message for the user

    Args:
        error_details: Detailed error information

    Returns:
        User-friendly error message
    """
    main_suggestion = error_details.suggestions[0] if error_details.suggestions else "Check logs for details"

    return (
        f"❌ Gemini API Error: {error_details.error_message}\n\n"
        f"💡 Quick Fix: {main_suggestion}\n\n"
        f"📋 Full debug info logged to console. "
        f"Check ComfyUI terminal for detailed traceback and all suggestions."
    )
