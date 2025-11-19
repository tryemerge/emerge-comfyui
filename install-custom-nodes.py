#!/usr/bin/env python3
"""
Local Custom Nodes Installer for ComfyUI Development
Mirrors the Docker custom nodes installation process with tier filtering
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Configuration
SCRIPT_DIR = Path(__file__).parent
COMFYUI_DIR = SCRIPT_DIR
CUSTOM_NODES_DIR = COMFYUI_DIR / "custom_nodes"
MONOREPO_CUSTOM_NODES = SCRIPT_DIR.parent / "comfyui-custom-nodes"
CONFIG_FILE = MONOREPO_CUSTOM_NODES / "config_nodes.json"


def print_header(text, char="━"):
    """Print a fancy header"""
    print(f"\n{char * 60}")
    print(f"   {text}")
    print(f"{char * 60}\n")


def run_command(cmd, cwd=None, check=True):
    """Run a shell command and return result"""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            check=check,
            capture_output=True,
            text=True,
            shell=True if isinstance(cmd, str) else False
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.CalledProcessError as e:
        return False, e.stdout, e.stderr


def install_node(node_config, tier_filter=None):
    """Install a single custom node"""
    name = node_config.get("name")
    url = node_config.get("url", "")
    requirements = node_config.get("requirements", False)
    tier = node_config.get("tier", 1)
    env_vars = node_config.get("env", {})
    custom_script = node_config.get("custom_script")
    recursive = node_config.get("recursive", False)

    # Skip if tier doesn't match filter
    if tier_filter is not None and tier != tier_filter:
        return False

    print(f"📦 Installing: {name} (tier {tier})")

    node_path = CUSTOM_NODES_DIR / name

    # Check if already exists
    if node_path.exists():
        if node_path.is_symlink():
            print(f"   ✅ Already installed (symlink)")
            return True
        elif (node_path / ".git").exists():
            print(f"   ✅ Already installed (git repo)")
            return True
        else:
            print(f"   ⚠️  Directory exists but not a git repo, skipping")
            return False

    # Parse git URL
    git_url = url
    if url.startswith("git clone "):
        git_url = url.replace("git clone ", "")

    # Clone the repository
    print(f"   🔄 Cloning from {git_url}")

    clone_cmd = ["git", "clone"]
    if recursive:
        clone_cmd.append("--recursive")
    clone_cmd.extend([git_url, str(node_path)])

    success, stdout, stderr = run_command(clone_cmd)

    if not success:
        print(f"   ❌ Clone failed: {stderr}")
        return False

    print(f"   ✅ Cloned successfully")

    # Run custom script if specified
    if custom_script:
        print(f"   🔧 Running custom script...")
        success, _, stderr = run_command(custom_script, cwd=node_path, check=False)
        if success:
            print(f"   ✅ Custom script completed")
        else:
            print(f"   ⚠️  Custom script failed: {stderr}")

    # Install Python requirements
    if requirements and (node_path / "requirements.txt").exists():
        print(f"   📋 Installing Python requirements...")
        success, _, stderr = run_command(
            [sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"],
            cwd=node_path
        )
        if success:
            print(f"   ✅ Requirements installed")
        else:
            print(f"   ⚠️  Requirements installation failed: {stderr}")

    # Create .env from template if needed
    env_example = node_path / ".env.example"
    env_file = node_path / ".env"

    if env_example.exists() and not env_file.exists():
        print(f"   📝 Creating .env from .env.example...")
        with open(env_example) as f:
            env_content = f.read()

        # Replace environment variables
        for key, value in env_vars.items():
            if value.startswith("${") and value.endswith("}"):
                env_var_name = value[2:-1]
                env_value = os.environ.get(env_var_name, "")
                if env_value:
                    env_content = env_content.replace(f"${{{env_var_name}}}", env_value)

        with open(env_file, "w") as f:
            f.write(env_content)

        print(f"   ✅ Created .env file")
        if any(v.startswith("${") for v in env_vars.values()):
            print(f"   ⚠️  Please check {env_file} and add any missing API keys")

    print()
    return True


def install_emprops_nodes():
    """Install EmProps custom nodes from monorepo via symlinks"""
    print_header("📦 Installing EmProps Custom Nodes (Tier 0)")

    installed = 0
    skipped = 0

    # Get all directories in monorepo custom nodes that look like EmProps nodes
    if not MONOREPO_CUSTOM_NODES.exists():
        print(f"⚠️  Monorepo custom nodes directory not found: {MONOREPO_CUSTOM_NODES}")
        return installed, skipped

    for node_dir in MONOREPO_CUSTOM_NODES.iterdir():
        if not node_dir.is_dir():
            continue
        if node_dir.name.startswith('.'):
            continue
        if node_dir.name in ['config_nodes.json', 'README.md']:
            continue

        node_name = node_dir.name
        node_path = CUSTOM_NODES_DIR / node_name

        print(f"📦 {node_name}")

        # Check if already installed
        if node_path.exists() and node_path.is_symlink():
            print(f"   ✅ Already symlinked")
            skipped += 1
            continue

        if node_path.exists():
            print(f"   ⚠️  Directory exists (not symlink), skipping")
            skipped += 1
            continue

        # Create symlink
        try:
            node_path.symlink_to(node_dir.absolute())
            print(f"   ✅ Created symlink")

            # Install requirements if they exist
            requirements_file = node_dir / "requirements.txt"
            if requirements_file.exists():
                print(f"   📋 Installing Python requirements...")
                success, _, stderr = run_command(
                    [sys.executable, "-m", "pip", "install", "-q", "-r", str(requirements_file)],
                    check=False
                )
                if success:
                    print(f"   ✅ Requirements installed")
                else:
                    print(f"   ⚠️  Requirements installation failed: {stderr}")

            installed += 1
        except Exception as e:
            print(f"   ❌ Failed to create symlink: {e}")

        print()

    return installed, skipped


def main():
    """Main installation function"""
    # Parse command line arguments
    tier_filter = None
    if len(sys.argv) > 1:
        try:
            tier_filter = int(sys.argv[1])
            print(f"🎯 Installing tier {tier_filter} nodes")
        except ValueError:
            print(f"❌ Invalid tier: {sys.argv[1]}")
            print("Usage: python install-custom-nodes.py [tier]")
            print("Examples:")
            print("  python install-custom-nodes.py 0  # EmProps nodes (symlink from monorepo)")
            print("  python install-custom-nodes.py 1  # Tier 1 external nodes (clone from git)")
            sys.exit(1)

    print_header("🔧 ComfyUI Custom Nodes Installer")
    print(f"📁 ComfyUI Directory: {COMFYUI_DIR}")
    print(f"📁 Custom Nodes Target: {CUSTOM_NODES_DIR}")
    print(f"📁 Monorepo Nodes: {MONOREPO_CUSTOM_NODES}")
    print(f"📋 Config File: {CONFIG_FILE}")
    print()

    # Create custom_nodes directory
    CUSTOM_NODES_DIR.mkdir(parents=True, exist_ok=True)

    installed = 0
    skipped = 0
    failed = 0

    # Handle tier 0 - EmProps nodes
    if tier_filter == 0:
        emprops_installed, emprops_skipped = install_emprops_nodes()
        installed += emprops_installed
        skipped += emprops_skipped

    # Handle tier 1+ - External nodes from config
    elif tier_filter is not None and tier_filter > 0:
        # Check if config file exists
        if not CONFIG_FILE.exists():
            print(f"❌ Error: config_nodes.json not found at {CONFIG_FILE}")
            sys.exit(1)

        # Load configuration
        with open(CONFIG_FILE) as f:
            config = json.load(f)

        nodes = config.get("custom_nodes", [])
        nodes_to_install = [n for n in nodes if n.get("tier") == tier_filter]

        print_header(f"📦 Installing Tier {tier_filter} External Nodes")
        print(f"Found {len(nodes_to_install)} tier {tier_filter} nodes to install\n")

        # Install each node
        for node in nodes_to_install:
            try:
                if install_node(node, tier_filter):
                    installed += 1
                else:
                    skipped += 1
            except Exception as e:
                print(f"   ❌ Error: {e}\n")
                failed += 1

    else:
        print("❌ Please specify a tier number")
        print("Usage: python install-custom-nodes.py [tier]")
        print("Examples:")
        print("  python install-custom-nodes.py 0  # EmProps nodes")
        print("  python install-custom-nodes.py 1  # Tier 1 external nodes")
        sys.exit(1)

    # Print summary
    print_header("✅ Installation Complete!")
    print(f"   Installed: {installed}")
    print(f"   Skipped:   {skipped}")
    print(f"   Failed:    {failed}")
    print()
    if tier_filter == 0:
        print("📝 Next steps:")
        print("   Run: python3 install-custom-nodes.py 1  # Install tier 1 external nodes")
    else:
        print("📝 Next steps:")
        print("   1. Check .env files in custom nodes that need API keys")
        print("   2. Set environment variables like OPENAI_API_KEY, GEMINI_API_KEY")
        print("   3. Run ComfyUI: python3 main.py --listen 0.0.0.0 --port 8188")
    print()


if __name__ == "__main__":
    main()
