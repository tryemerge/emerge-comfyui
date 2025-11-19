# ComfyUI Upstream Management

This directory contains the ComfyUI codebase integrated into the monorepo. It includes critical websocket support patches on top of the official ComfyUI.

## Git Remotes

- **`comfyui-official`**: Official ComfyUI repository (https://github.com/comfyanonymous/ComfyUI.git)
- **`comfyui-upstream`**: Original fork with websocket support (DEPRECATED - can be deleted after integration complete)

## Pulling Upstream Updates

To merge the latest changes from official ComfyUI:

```bash
# 1. Fetch latest from official ComfyUI
git fetch comfyui-official master

# 2. Pull updates into packages/comfyui using subtree
git subtree pull --prefix=packages/comfyui comfyui-official master --squash

# 3. Resolve any conflicts (especially around websocket patches)
# Your websocket modifications will need to be preserved during merge

# 4. Test the merge
cd apps/machine
docker-compose --profile comfyui-dev up --build

# 5. Commit the merge
git add packages/comfyui
git commit -m "chore(comfyui): merge upstream v0.3.XX from official ComfyUI"
```

## Tracking Specific Versions

To pull a specific version tag:

```bash
# Fetch tags
git fetch comfyui-official --tags

# Pull specific version
git subtree pull --prefix=packages/comfyui comfyui-official v0.3.66 --squash
```

## Critical Patches to Preserve

Our fork contains **critical websocket support** that must be preserved when merging upstream changes. Key areas to watch during merges:

- WebSocket event handling and real-time job monitoring
- Custom API endpoints for job status
- Any modifications to the ComfyUI server startup/routing

## Conflict Resolution Strategy

When conflicts occur during upstream merges:

1. **Identify the conflicting files** - likely in websocket-related code
2. **Preserve our websocket patches** - these are critical for the job broker
3. **Accept upstream changes** for everything else (bug fixes, new features)
4. **Test thoroughly** - ensure websocket monitoring still works after merge
5. **Document changes** - note any significant upstream changes in changelog

## Testing After Upstream Merge

```bash
# 1. Build and start dev ComfyUI
cd apps/machine
docker-compose --profile comfyui-dev up --build

# 2. Access ComfyUI at http://localhost:8188

# 3. Test websocket connectivity
# - Check real-time job monitoring in apps/monitor
# - Submit a test job and verify events flow correctly
# - Check PM2 service logs for any errors

# 4. Test basic workflow execution
# - Load a workflow in ComfyUI UI
# - Generate an image
# - Verify output appears correctly
```

## Version Strategy

- **Production**: Pin to tested versions (update cautiously)
- **Development**: Can track closer to latest official releases
- **Custom Nodes**: Managed separately, may have their own version constraints

## Future Upstream Relationship

Once `packages/comfyui` is fully integrated:
1. Delete the `stakeordie/ComfyUI` fork repository on GitHub
2. Remove the `comfyui-upstream` git remote from this repo
3. Track only `comfyui-official` for future updates
4. Maintain websocket patches as part of the monorepo
