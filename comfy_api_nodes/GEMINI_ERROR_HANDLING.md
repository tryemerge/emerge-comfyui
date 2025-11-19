# Gemini Node Error Handling

## Overview

The Gemini nodes now have comprehensive error handling that provides crystal-clear debugging information when failures occur. No more "didn't return anything" errors - you'll get detailed diagnostics and actionable suggestions.

## What's New

### Enhanced Error Reporting

When a Gemini node fails, you now get:

1. **Detailed Error Breakdown** - Logged to console with full context
2. **Actionable Suggestions** - Specific fixes based on the error type
3. **Full Request/Response Details** - See exactly what was sent and received
4. **Sanitized Debug Info** - API keys redacted, large data truncated
5. **User-Friendly Messages** - Clear, concise error shown in ComfyUI

### Example Error Output

#### In Console (Full Details)
```
================================================================================
🚨 GEMINI NODE EXECUTION FAILED
================================================================================
Timestamp: 2025-11-16T12:34:56.789Z
Error Type: ValueError

ERROR MESSAGE:
Gemini API returned no candidates. This typically indicates content filtering,
API quota/rate limits, or invalid request. Check the full response above for details.

GEMINI API RESPONSE:
{
  "promptFeedback": {
    "blockReason": "SAFETY",
    "safetyRatings": [
      {
        "category": "HARM_CATEGORY_HATE_SPEECH",
        "probability": "HIGH"
      }
    ]
  },
  "candidates": null
}

REQUEST DETAILS:
{
  "model": "gemini-2.5-pro",
  "prompt": "Analyze this image...",
  "has_images": true,
  "has_audio": false,
  "has_video": false,
  "has_files": false,
  "parts_count": 2,
  "seed": 42
}

SUGGESTED FIXES:
  1. Check if your content violates Google's safety policies
  2. Verify your GEMINI_API_KEY is valid and has not exceeded quota
  3. Try reducing input size (large images/videos may be rejected)
  4. Check Gemini API status: https://status.cloud.google.com/
  5. Review the full Gemini response above for 'promptFeedback' or 'safetyRatings'

FULL TRACEBACK:
[Full Python traceback here]
================================================================================
```

#### In ComfyUI (User Message)
```
❌ Gemini API Error: Gemini API returned no candidates

💡 Quick Fix: Check if your content violates Google's safety policies

📋 Full debug info logged to console. Check ComfyUI terminal for detailed
   traceback and all suggestions.
```

## Common Error Scenarios

### 1. Content Safety Filtering

**Error**: "Gemini API returned no candidates"

**Cause**: Google's safety filters blocked the content

**Suggestions**:
- Review Google's content policies
- Check if images contain sensitive content
- Try with different prompts
- Review `promptFeedback.safetyRatings` in console output

### 2. API Key Issues

**Error**: "GEMINI_API_KEY environment variable is required"

**Cause**: Missing or invalid API key

**Suggestions**:
- Set GEMINI_API_KEY environment variable
- Get a new key from https://aistudio.google.com/apikey
- Check if key is expired or revoked
- Verify key has Gemini API enabled

### 3. Rate Limiting

**Error**: Contains "quota", "rate", or "429"

**Cause**: Exceeded API rate limits

**Suggestions**:
- Wait before retrying
- Check quota in Google Cloud Console
- Consider upgrading to paid plan
- Implement exponential backoff

### 4. Network/Timeout Errors

**Error**: Contains "timeout" or "connection"

**Cause**: Network connectivity issues

**Suggestions**:
- Check internet connection
- Verify firewall settings
- Try with smaller inputs
- Retry after a few seconds

### 5. Invalid Request

**Error**: HTTP 400 or "invalid"

**Cause**: Malformed request or unsupported parameters

**Suggestions**:
- Check request format matches API spec
- Verify all required fields present
- Check for unsupported MIME types
- Ensure prompt is not empty

## Debugging Workflow

### Step 1: Check Console Logs

Look for the detailed error report starting with:
```
🚨 GEMINI NODE EXECUTION FAILED
```

### Step 2: Review Suggestions

The error handler provides 3-5 specific suggestions based on the error type. Try them in order.

### Step 3: Examine Request Details

Check what was sent to the API:
- Model name
- Prompt text (truncated for privacy)
- Input types (images, audio, video, files)
- Part count

### Step 4: Analyze API Response

If available, review the full Gemini API response for:
- `promptFeedback` - Why the request was rejected
- `safetyRatings` - Which safety filters triggered
- `candidates` - Whether any results were returned
- Error codes - HTTP status or API error codes

## Implementation Details

### Error Handler Module

Location: `packages/comfyui/comfy_api_nodes/gemini_error_handler.py`

**Key Functions**:
- `create_error_details()` - Extract comprehensive error info
- `log_error_details()` - Format and log to console
- `create_user_friendly_error_message()` - Generate UX-friendly message
- `generate_suggestions()` - Pattern-match errors to solutions

### Modified Nodes

Both `GeminiNode` and `GeminiImageNode` now wrap their `api_call()` methods with try/catch blocks that:

1. Capture the exception
2. Collect request/response context
3. Generate detailed error report
4. Log to console
5. Re-raise with user-friendly message

### Data Sanitization

The error handler automatically sanitizes sensitive data:
- API keys → `***REDACTED***`
- Base64 image data → `<base64 data: N bytes>`
- Long prompts → Truncated to 200 chars

This makes error reports safe to share for debugging.

## For Developers

### Adding Custom Error Patterns

To add new error detection patterns, edit `gemini_error_handler.py`:

```python
def generate_suggestions(
    error_type: str,
    error_message: str,
    response: Optional[dict],
) -> list[str]:
    suggestions = []

    # Add your pattern here
    if "your_error_pattern" in error_message.lower():
        suggestions.extend([
            "Suggestion 1 for this error",
            "Suggestion 2 for this error",
        ])

    return suggestions
```

### Testing Error Handling

To test error scenarios:

```python
# Test API key missing
os.environ['GEMINI_API_KEY'] = ''

# Test safety filtering (use problematic content)
prompt = "content that triggers safety filters"

# Test quota limit (make many requests quickly)
for i in range(100):
    await node.api_call(...)

# Test network error (disconnect internet)
# Test invalid model (use fake model name)
model = "gemini-fake-model"
```

## Monitoring & Logs

### Where to Find Logs

**Local Development**:
```bash
# ComfyUI console output
tail -f /path/to/comfyui/logs/console.log

# Or run ComfyUI in foreground
python main.py
```

**Production (Railway)**:
```bash
# View PM2 logs
pm2 logs comfyui-gpu0

# Or via Railway CLI
railway logs
```

### Log Format

All Gemini node logs are prefixed with:
- `[EmProps GeminiNode]` - Text generation node
- `[EmProps GeminiImageNode]` - Image generation node

Success logs:
```
[EmProps GeminiNode] Making API call with model=gemini-2.5-pro, parts_count=2
[EmProps GeminiNode] Received response, processing...
[EmProps GeminiNode] Success! Output length: 1234 chars
```

Error logs:
```
[EmProps GeminiNode] ERROR
🚨 GEMINI NODE EXECUTION FAILED
...
```

## Best Practices

### For Users

1. **Always check console** - Full error details are there
2. **Follow suggestions** - They're specific to your error
3. **Share error reports** - Safe to share (API keys redacted)
4. **Check API status** - Google services may be down

### For Developers

1. **Don't suppress errors** - Let them bubble up with context
2. **Add logging** - Use `print()` for milestone logging
3. **Test error paths** - Simulate failures to verify error messages
4. **Update suggestions** - Add new patterns as you discover them

## Future Improvements

Potential enhancements:

- [ ] Retry logic with exponential backoff
- [ ] Error telemetry/analytics
- [ ] Slack/email notifications for production errors
- [ ] Error recovery strategies (fallback models, cached responses)
- [ ] Structured error codes for programmatic handling

## Support

If you encounter errors not covered by the suggestions:

1. Check the full console output
2. Review [Gemini API docs](https://ai.google.dev/gemini-api/docs)
3. Check [API status page](https://status.cloud.google.com/)
4. Share the sanitized error report with the team
