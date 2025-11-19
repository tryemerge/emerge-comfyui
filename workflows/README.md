# ComfyUI Workflows

This directory contains version-controlled ComfyUI workflows for the emp-job-queue system.

## Directory Structure

```
workflows/
├── production/          # Production workflows used by the job broker
├── templates/           # Reusable workflow templates
├── examples/            # Example workflows for testing
└── README.md           # This file
```

## Workflow Organization

### Production Workflows (`production/`)

These workflows are used by the production job broker and should be:
- **Tested thoroughly** before committing
- **Documented** with clear descriptions
- **Versioned** - use semantic versioning in filenames if needed
- **Stable** - breaking changes require migration planning

Example:
```
production/
├── text-to-image-v1.json          # Basic text-to-image generation
├── video-generation-v2.json       # Video generation workflow
└── upscale-and-enhance-v1.json    # Image upscaling
```

### Templates (`templates/`)

Reusable workflow patterns that can be customized:
```
templates/
├── basic-sdxl-template.json       # SDXL base template
├── controlnet-template.json       # ControlNet template
└── video-base-template.json       # Video generation base
```

### Examples (`examples/`)

Test workflows and demonstrations:
```
examples/
├── simple-test-workflow.json      # Quick smoke test
├── gpu-stress-test.json           # Performance testing
└── node-compatibility-test.json   # Custom node testing
```

## Using Workflows

### In Development

When running `docker-compose -f docker-compose.dev.yml up comfyui-dev`:

1. **Workflows are auto-loaded** from this directory into `/workspace/ComfyUI/workflows/`
2. **Edit in ComfyUI UI** - changes are automatically saved to the persistent volume
3. **Export to monorepo** - manually copy important workflows back to this directory for version control

### Loading Workflows

In ComfyUI UI:
1. Click "Load" button
2. Navigate to `/workspace/ComfyUI/workflows/`
3. Select your workflow

Or use the ComfyUI API:
```bash
curl -X POST http://localhost:8188/prompt \
  -H "Content-Type: application/json" \
  -d @packages/comfyui/workflows/production/text-to-image-v1.json
```

### Saving New Workflows

**For version control**:
1. Create/test workflow in ComfyUI UI
2. Export as JSON (Save button in UI)
3. Copy the JSON file to appropriate directory:
   ```bash
   # From inside container
   docker exec comfyui-dev cp /workspace/ComfyUI/user/my-workflow.json /workspace/ComfyUI/workflows/production/

   # From host (dev workflow)
   cp ~/Downloads/my-workflow.json packages/comfyui/workflows/production/
   ```
4. Commit to git

## Workflow Best Practices

### Naming Convention

Use descriptive names with version numbers:
- `{purpose}-v{version}.json` - e.g., `text-to-image-v2.json`
- `{model}-{task}-v{version}.json` - e.g., `sdxl-upscale-v1.json`

### Documentation

Each workflow should include metadata (if possible):
```json
{
  "last_node_id": 10,
  "last_link_id": 15,
  "_meta": {
    "description": "SDXL text-to-image with LoRA",
    "version": "1.0.0",
    "author": "team",
    "requirements": ["sdxl-base-1.0", "custom-lora-v1"],
    "tested": "2025-01-27"
  }
}
```

### Model Dependencies

Document required models in workflow comments or separate `models.md`:
- Base models (SDXL, SD1.5, etc.)
- LoRAs
- VAEs
- ControlNet models
- Custom models

### Testing

Before committing production workflows:
1. Test with different inputs
2. Verify outputs are as expected
3. Check resource usage (GPU memory, time)
4. Test error handling (missing inputs, invalid parameters)

## Integration with Job Broker

The job broker can reference workflows by path:

```javascript
// Job submission
{
  "workflow_path": "production/text-to-image-v1.json",
  "inputs": {
    "prompt": "a beautiful landscape",
    "seed": 42
  }
}
```

## Workflow Versioning

When updating workflows:

1. **Minor changes** (parameter tweaks):
   - Update existing file
   - Document changes in commit message

2. **Major changes** (different nodes, structure):
   - Create new version: `workflow-v2.json`
   - Keep old version for backward compatibility
   - Add migration notes

3. **Breaking changes**:
   - Plan deprecation of old version
   - Update job broker to use new version
   - Remove old version after migration period

## Syncing Workflows

### From Container to Monorepo (Export)

```bash
# Copy specific workflow
docker cp comfyui-dev:/workspace/ComfyUI/user/my-workflow.json packages/comfyui/workflows/production/

# Copy all user workflows
docker cp comfyui-dev:/workspace/ComfyUI/user/. packages/comfyui/workflows/backup/
```

### From Monorepo to Container (Import)

This happens automatically via volume mount in development mode.

For production containers:
```bash
docker cp packages/comfyui/workflows/production/workflow.json container-name:/workspace/ComfyUI/workflows/
```

## CI/CD Integration

Future: Automated workflow validation
- JSON schema validation
- Node compatibility checks
- Model dependency verification
- Performance benchmarking

## Related Documentation

- [ComfyUI Custom Nodes](../custom_nodes/README.md)
- [Model Management](../models/README.md)
- [Job Broker Integration](../../../apps/api/docs/workflows.md)
