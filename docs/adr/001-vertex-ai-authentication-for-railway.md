# ADR 001: Vertex AI Authentication for Railway Deployments

**Status:** Proposed
**Date:** 2025-11-21
**Decision Makers:** Engineering Team
**Technical Story:** Enable Vertex AI access for Gemini nodes on Railway deployments to access premium models and utilize $100K GCP credits

---

## Context and Problem Statement

We need to authenticate ComfyUI instances running on Railway.app to Google Cloud Vertex AI to access premium Gemini models (specifically `gemini-3-pro-image-preview` aka "Nano Banana 2"). The organization has $100K in GCP credits available, making Vertex AI the preferred choice over AI Studio's rate-limited free tier.

**Key Requirements:**
1. Railway instances are ephemeral - they spin up and kill frequently in automated deployments
2. Authentication must work non-interactively (no browser-based `gcloud auth` flows)
3. Solution must be simple to implement and debug
4. Implementation must remain merge-friendly with upstream ComfyUI changes

**Initial Constraint (Later Resolved):**
- Organization policy `iam.disableServiceAccountKeyCreation` was initially enforced
- After evaluation, we determined requesting a policy exception is the pragmatic path forward

---

## Decision Drivers

1. **Simplicity:** Solution should be straightforward to set up, debug, and maintain
2. **Deployment Automation:** Must work in Railway's ephemeral, containerized environment
3. **Credential Management:** Credentials must be manageable through Railway's environment variable system
4. **Cost Efficiency:** Leverage existing $100K GCP credits instead of paying for AI Studio usage
5. **Upstream Compatibility:** Must not create merge conflicts with official ComfyUI updates
6. **Time to Production:** Minimize setup complexity to ship faster

---

## Considered Options

### Option 1: Service Account Keys ✅ (Selected)

**Description:** Generate service account JSON key file, store in Railway environment variable

**How It Works:**

```
┌─────────────────────────────────────────────────────────────────┐
│                    Railway Container Boot                        │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  1. Load service account key JSON from environment variable      │
│     GOOGLE_APPLICATION_CREDENTIALS_JSON (stored in Railway)      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  2. Write JSON to filesystem at /tmp/gcp-creds.json              │
│     Set GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-creds.json       │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  3. google-auth library reads key file                           │
│     - Extracts private key and service account email             │
│     - Signs JWT for authentication                               │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  4. Library exchanges signed JWT for access token                │
│     POST to oauth2.googleapis.com/token                          │
│     Returns: Bearer access token (1 hour TTL)                    │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  5. ComfyUI uses access token to call Vertex AI API              │
│     Authorization: Bearer <access_token>                         │
│     POST to aiplatform.googleapis.com/.../models/gemini-3:gen... │
└─────────────────────────────────────────────────────────────────┘
```

**Pros:**
- ✅ Simple to implement (5 minutes)
- ✅ Well-documented, battle-tested approach
- ✅ Works perfectly with Railway environment variables
- ✅ Easy to debug - if key works locally, it works on Railway
- ✅ No external dependencies beyond GCP APIs

**Cons:**
- ⚠️ Requires org policy exception for `iam.disableServiceAccountKeyCreation`
- ⚠️ Long-lived credentials (must rotate every 90 days)
- ⚠️ If key leaks, attacker has access until key is revoked

**Mitigations:**
- Use dedicated service account with minimal permissions (`roles/aiplatform.user` only)
- Set up 90-day key rotation reminder
- Never commit key to git (Railway env vars only)
- Monitor Cloud Audit Logs for unusual activity

**Verdict:** ✅ **Selected** - Simplest path to production with acceptable risk profile

---

### Option 2: Workload Identity Federation

**Description:** Use GCP Workload Identity Federation to exchange external identity tokens for GCP access tokens

**Pros:**
- No service account keys required
- Short-lived tokens (1 hour) auto-rotate
- More secure if properly configured

**Cons:**
- ❌ Railway doesn't have native identity provider integration with GCP
- ❌ Requires setting up custom OIDC provider or workarounds
- ❌ Significantly more complex to implement and debug
- ❌ The "external token" problem - Railway can't natively prove its identity to GCP

**Verdict:** ❌ **Rejected** - Overcomplicated for Railway. WIF is designed for AWS/Azure/GitHub Actions workloads that have native identity providers. Railway would require additional infrastructure to make this work.

---

### Option 3: Interactive gcloud Authentication

**Description:** Use `gcloud auth application-default login` on instances

**Pros:**
- No credential files needed
- Uses standard OAuth flow

**Cons:**
- ❌ Requires interactive browser login
- ❌ Incompatible with automated deployments
- ❌ Not suitable for ephemeral Railway containers

**Verdict:** ❌ **Rejected** - Not viable for automated deployments

---

### Option 4: GCP Metadata Service

**Description:** Run on GCP infrastructure (GCE/GKE/Cloud Run) and use metadata service

**Pros:**
- No credential distribution needed
- Automatic token rotation
- Most secure option

**Cons:**
- ❌ Requires migration from Railway to GCP
- ❌ Different deployment model
- ❌ Not using Railway

**Verdict:** ❌ **Rejected** - Requires infrastructure migration

---

## Decision Outcome

**Chosen option:** "Service Account Keys" because:

1. **Simplest Implementation:** 5 minutes vs 60+ minutes for WIF
2. **Battle-Tested:** Standard pattern used by most Railway→GCP deployments
3. **Easy Debugging:** Key works locally = key works on Railway
4. **Acceptable Risk:** Dedicated service account with minimal permissions, key rotation, audit logging
5. **Org Policy Exception is Reasonable:** A single service account for a specific purpose is a valid exception request

---

## Implementation Guide

### Phase 1: Request Org Policy Exception

Send this to your GCP admin / security team:

```
Subject: Request for Service Account Key Creation Exception

Hi [Admin],

I'm requesting an exception to the iam.disableServiceAccountKeyCreation
policy for a single service account to enable Vertex AI access from our
Railway deployments.

Details:
- Service Account: railway-vertex-ai@[PROJECT_ID].iam.gserviceaccount.com
- Purpose: Access Vertex AI Gemini models from Railway.app containers
- Permissions: roles/aiplatform.user only (minimal required permissions)
- Security Controls:
  - Key stored only in Railway encrypted environment variables
  - 90-day key rotation schedule
  - Cloud Audit Logs monitoring enabled
  - Never committed to source control

Why we need this:
- Railway doesn't support GCP Workload Identity Federation natively
- We have $100K in GCP credits to utilize
- Vertex AI provides access to models not available via AI Studio

Risk Mitigation:
- Single-purpose service account (not shared)
- Minimal IAM permissions
- Key rotation policy
- Audit logging for anomaly detection

Please let me know if you need additional information.

Thanks,
[Your Name]
```

### Phase 2: GCP Setup (5 minutes)

Once exception is approved:

```bash
# Set your project ID
export GCP_PROJECT_ID="your-project-id"

# 1. Create dedicated service account
gcloud iam service-accounts create railway-vertex-ai \
    --project="${GCP_PROJECT_ID}" \
    --display-name="Railway Vertex AI Access" \
    --description="Service account for ComfyUI on Railway to access Vertex AI"

# 2. Grant ONLY Vertex AI User permissions (least privilege)
gcloud projects add-iam-policy-binding "${GCP_PROJECT_ID}" \
    --member="serviceAccount:railway-vertex-ai@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/aiplatform.user"

# 3. Create the key file
gcloud iam service-accounts keys create railway-vertex-ai-key.json \
    --iam-account="railway-vertex-ai@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

# 4. View the key (you'll copy this to Railway)
cat railway-vertex-ai-key.json

# 5. IMPORTANT: Delete local key file after copying to Railway
rm railway-vertex-ai-key.json
```

### Phase 3: Railway Configuration (2 minutes)

1. Go to your Railway project → Settings → Variables
2. Add these environment variables:

```bash
# Paste the ENTIRE contents of the JSON key file (single line)
GOOGLE_APPLICATION_CREDENTIALS_JSON={"type":"service_account","project_id":"...","private_key_id":"...","private_key":"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n","client_email":"railway-vertex-ai@PROJECT.iam.gserviceaccount.com","client_id":"...","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs","client_x509_cert_url":"..."}

# Your GCP project ID
GCP_PROJECT_ID=your-project-id

# Vertex AI region (us-central1 has best model availability)
GCP_REGION=us-central1
```

### Phase 4: Startup Script

Update your Railway start command or create a startup script:

**Option A: Procfile**
```
web: bash -c 'echo "$GOOGLE_APPLICATION_CREDENTIALS_JSON" > /tmp/gcp-creds.json && export GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-creds.json && python main.py'
```

**Option B: startup.sh script**
```bash
#!/bin/bash
set -e

# Write GCP credentials to filesystem
if [ -n "$GOOGLE_APPLICATION_CREDENTIALS_JSON" ]; then
    echo "$GOOGLE_APPLICATION_CREDENTIALS_JSON" > /tmp/gcp-creds.json
    export GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-creds.json
    echo "[Startup] GCP credentials configured for Vertex AI"
fi

# Start the application
exec python main.py
```

### Phase 5: Verify It Works

After deploying, check Railway logs for:
```
[EmProps Gemini] Authentication backend: vertex_ai
```

This confirms the application detected Vertex AI credentials.

---

## Application Code (Already Implemented)

The authentication abstraction layer is already in place at [gemini_auth.py](../../comfy_api_nodes/gemini_auth.py):

```python
def is_vertex_ai_configured() -> bool:
    """Check if Vertex AI credentials are configured."""
    if not GCP_PROJECT_ID:
        return False
    if GOOGLE_APPLICATION_CREDENTIALS:
        return True
    # Also check for ADC
    try:
        import google.auth
        credentials, project = google.auth.default()
        return True
    except Exception:
        return False

def get_vertex_ai_access_token() -> str:
    """Get OAuth2 access token for Vertex AI."""
    import google.auth
    from google.auth.transport.requests import Request

    credentials, project = google.auth.default(
        scopes=['https://www.googleapis.com/auth/cloud-platform']
    )

    if not credentials.valid:
        credentials.refresh(Request())

    return credentials.token
```

The `google.auth.default()` function automatically:
- Detects service account key from `GOOGLE_APPLICATION_CREDENTIALS`
- Loads credentials and signs JWTs
- Exchanges for access tokens
- Handles automatic refresh when expired

---

## Security Considerations

### What the Service Account Key Contains

```json
{
  "type": "service_account",
  "project_id": "your-project",
  "private_key_id": "abc123...",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...",  // ⚠️ THIS IS THE SECRET
  "client_email": "railway-vertex-ai@project.iam.gserviceaccount.com",
  "client_id": "123456789",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  ...
}
```

The `private_key` field is the sensitive part. If leaked, an attacker can authenticate as this service account.

### Security Controls

| Control | Implementation |
|---------|----------------|
| **Least Privilege** | Service account only has `roles/aiplatform.user` |
| **No Git Commits** | Key only in Railway env vars, never in code |
| **Key Rotation** | 90-day rotation schedule (calendar reminder) |
| **Audit Logging** | Cloud Audit Logs enabled for service account activity |
| **Dedicated Account** | Not shared with other services |
| **Railway Encryption** | Railway encrypts environment variables at rest |

### Key Rotation Procedure (Every 90 Days)

```bash
# 1. Create new key
gcloud iam service-accounts keys create new-key.json \
    --iam-account="railway-vertex-ai@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

# 2. Update Railway environment variable with new key

# 3. Verify deployment works with new key

# 4. Delete old key from GCP
gcloud iam service-accounts keys list \
    --iam-account="railway-vertex-ai@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
# Find the OLD key ID, then:
gcloud iam service-accounts keys delete OLD_KEY_ID \
    --iam-account="railway-vertex-ai@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

# 5. Delete local key file
rm new-key.json
```

---

## Troubleshooting

### Error: "Could not automatically determine credentials"

**Cause:** `GOOGLE_APPLICATION_CREDENTIALS` not set or file doesn't exist

**Fix:**
1. Verify `GOOGLE_APPLICATION_CREDENTIALS_JSON` env var is set in Railway
2. Verify startup script writes it to `/tmp/gcp-creds.json`
3. Verify `GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-creds.json` is exported

### Error: "Permission denied" on Vertex AI API

**Cause:** Service account missing `roles/aiplatform.user`

**Fix:**
```bash
gcloud projects add-iam-policy-binding "${GCP_PROJECT_ID}" \
    --member="serviceAccount:railway-vertex-ai@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/aiplatform.user"
```

### Error: "Invalid JWT" or "Token refresh failed"

**Cause:** Corrupted or truncated JSON key

**Fix:**
1. Regenerate the key in GCP Console
2. Copy the ENTIRE JSON (including all brackets)
3. Ensure no line breaks were added when pasting into Railway

### Checking Authentication Locally

```bash
# Test that your key works
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
export GCP_PROJECT_ID=your-project
export GCP_REGION=us-central1

python -c "
from comfy_api_nodes.gemini_auth import get_auth_backend, get_vertex_ai_access_token
print(f'Backend: {get_auth_backend()}')
token = get_vertex_ai_access_token()
print(f'Token obtained: {token[:20]}...')
"
```

---

## Monitoring

### Enable Cloud Audit Logs

```bash
# View recent authentications by the service account
gcloud logging read "
  protoPayload.authenticationInfo.principalEmail=\"railway-vertex-ai@${GCP_PROJECT_ID}.iam.gserviceaccount.com\"
" --limit=10 --project="${GCP_PROJECT_ID}"
```

### Set Up Alerts (Optional)

In GCP Console → Monitoring → Alerting, create alert for:
- Unusual API call volume from service account
- Calls from unexpected IP ranges
- Failed authentication attempts

---

## Comparison Summary

| Factor | Service Account Keys | Workload Identity Federation |
|--------|---------------------|------------------------------|
| **Setup Time** | 5 minutes | 60+ minutes |
| **Complexity** | Low | High |
| **Railway Support** | ✅ Native | ⚠️ Requires workarounds |
| **Debugging** | Easy | Complex |
| **Security** | Good (with controls) | Better (short-lived tokens) |
| **Org Policy** | Needs exception | Compliant |
| **Recommendation** | ✅ Use this | Only if keys are truly blocked |

---

## References

- [GCP Service Account Keys](https://cloud.google.com/iam/docs/keys-create-delete)
- [google-auth Python Library](https://google-auth.readthedocs.io/)
- [Railway Environment Variables](https://docs.railway.app/guides/variables)
- [Vertex AI Authentication](https://cloud.google.com/vertex-ai/docs/authentication)
- [GCP IAM Best Practices](https://cloud.google.com/iam/docs/best-practices-for-securing-service-accounts)

---

---

## Integration Guide for emerge_turbo

> **For AI Agents:** This section provides step-by-step instructions for integrating the Vertex AI authentication code from emerge-comfyui into the emerge_turbo monorepo.

### Files to Copy

Copy these files from `emerge-comfyui` to the appropriate location in `emerge_turbo`:

| Source (emerge-comfyui) | Purpose |
|------------------------|---------|
| `comfy_api_nodes/gemini_auth.py` | Authentication abstraction layer - handles both AI Studio and Vertex AI |
| `comfy_api_nodes/gemini_error_handler.py` | Error handling with JSON serialization fixes |

### Dependencies to Add

Add to your `requirements.txt` or `pyproject.toml`:

```
google-auth>=2.23.0
```

### Environment Variables Required

The following environment variables must be configured in Railway:

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | For Vertex AI | Full JSON contents of GCP service account key |
| `GCP_PROJECT_ID` | For Vertex AI | GCP project ID (e.g., `my-project-123`) |
| `GCP_REGION` | For Vertex AI | Vertex AI region (default: `us-central1`) |
| `GEMINI_API_KEY` | For AI Studio | Alternative: use AI Studio instead of Vertex AI |

**Note:** Set either `GEMINI_API_KEY` (for AI Studio) OR the GCP variables (for Vertex AI), not both. The code auto-detects which backend to use.

### Railway Startup Configuration

**CRITICAL:** The service account JSON must be written to the filesystem before the app starts. Add this to your Railway startup:

**Option 1: Inline in Railway start command**
```bash
bash -c 'if [ -n "$GOOGLE_APPLICATION_CREDENTIALS_JSON" ]; then echo "$GOOGLE_APPLICATION_CREDENTIALS_JSON" > /tmp/gcp-creds.json && export GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-creds.json; fi && python main.py'
```

**Option 2: Startup script (recommended for emerge_turbo)**
Create or update your entrypoint script:

```bash
#!/bin/bash
set -e

# Configure GCP credentials for Vertex AI
if [ -n "$GOOGLE_APPLICATION_CREDENTIALS_JSON" ]; then
    echo "$GOOGLE_APPLICATION_CREDENTIALS_JSON" > /tmp/gcp-creds.json
    export GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-creds.json
    echo "[Startup] Vertex AI credentials configured"
fi

# Start your application
exec "$@"
```

### How to Use in Code

```python
from gemini_auth import get_gemini_endpoint, get_auth_backend

# Check which backend is active (for logging/debugging)
backend = get_auth_backend()  # Returns "vertex_ai" or "ai_studio"
print(f"Using Gemini backend: {backend}")

# Get a configured endpoint for API calls
endpoint = get_gemini_endpoint(model="gemini-2.5-flash", action="generateContent")

# endpoint.path = full URL (AI Studio or Vertex AI depending on config)
# endpoint.headers = {"Authorization": "Bearer <token>"} for Vertex AI
# endpoint.query_params = {"key": "<api_key>"} for AI Studio
```

### Authentication Flow Summary

```
┌─────────────────────────────────────────────────────────────┐
│                   Application Startup                        │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  get_auth_backend() checks environment:                      │
│                                                              │
│  1. Is GCP_PROJECT_ID set AND credentials available?         │
│     → Return "vertex_ai"                                     │
│                                                              │
│  2. Is GEMINI_API_KEY set?                                   │
│     → Return "ai_studio"                                     │
│                                                              │
│  3. Neither configured?                                      │
│     → Raise ValueError with helpful message                  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  get_gemini_endpoint(model, action) returns:                 │
│                                                              │
│  For Vertex AI:                                              │
│    path: https://{region}-aiplatform.googleapis.com/v1/...   │
│    headers: {"Authorization": "Bearer <access_token>"}       │
│                                                              │
│  For AI Studio:                                              │
│    path: https://generativelanguage.googleapis.com/v1beta/..│
│    query_params: {"key": "<api_key>"}                        │
└─────────────────────────────────────────────────────────────┘
```

### Verification Steps

After integration, verify with these checks:

**1. Local test (AI Studio):**
```bash
export GEMINI_API_KEY=your-test-key
python -c "from gemini_auth import get_auth_backend; print(get_auth_backend())"
# Expected output: ai_studio
```

**2. Local test (Vertex AI):**
```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
export GCP_PROJECT_ID=your-project
export GCP_REGION=us-central1
python -c "from gemini_auth import get_auth_backend; print(get_auth_backend())"
# Expected output: vertex_ai
```

**3. Railway deployment:**
Check logs for: `[EmProps Gemini] Authentication backend: vertex_ai`

### Common Integration Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| `ImportError: google.auth` | Missing dependency | Add `google-auth>=2.23.0` to requirements |
| `ValueError: No Gemini authentication configured` | Missing env vars | Set either `GEMINI_API_KEY` or GCP variables |
| `DefaultCredentialsError` | JSON not written to file | Ensure startup script runs before app import |
| `PermissionDenied` on API call | Service account missing role | Grant `roles/aiplatform.user` to service account |

### Models Available via Vertex AI

With Vertex AI configured, you gain access to:

**Text Models:**
- `gemini-3-pro-preview` - Latest Gemini 3 model
- `gemini-2.5-pro` / `gemini-2.5-flash` - Production models
- `gemini-2.5-pro-preview-05-06` / `gemini-2.5-flash-preview-04-17` - Preview versions

**Image Models (Nano Banana):**
- `gemini-2.5-flash-image` - "Nano Banana Pro" (production)
- `gemini-2.5-flash-image-preview` - Preview version

### Security Reminders

1. **Never commit** the service account key JSON to git
2. **Rotate keys** every 90 days (set a calendar reminder)
3. **Monitor** Cloud Audit Logs for unusual activity
4. **Least privilege**: Service account should only have `roles/aiplatform.user`

---

## Document History

- 2025-11-21: Initial draft - chose WIF approach
- 2025-11-21: Revised to service account keys after evaluating WIF complexity for Railway
- 2025-11-21: Added integration guide for emerge_turbo agents
- 2025-11-21: Updated available models list after upstream merge (v0.3.71)
