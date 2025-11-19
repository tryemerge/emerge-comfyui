#!/bin/bash
# Local Custom Nodes Installer for ComfyUI Development
# Mirrors the Docker custom nodes installation process

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMFYUI_DIR="$SCRIPT_DIR"
CUSTOM_NODES_DIR="$COMFYUI_DIR/custom_nodes"
MONOREPO_CUSTOM_NODES="../comfyui-custom-nodes"

echo "🔧 Installing ComfyUI Custom Nodes for Local Development"
echo "📁 ComfyUI Directory: $COMFYUI_DIR"
echo "📁 Custom Nodes Target: $CUSTOM_NODES_DIR"
echo ""

# Create custom_nodes directory if it doesn't exist
mkdir -p "$CUSTOM_NODES_DIR"

# Function to install a single custom node
install_node() {
    local node_name="$1"
    local node_path="$CUSTOM_NODES_DIR/$node_name"
    local source_path="$MONOREPO_CUSTOM_NODES/$node_name"

    echo "📦 Installing: $node_name"

    # Check if node exists in monorepo
    if [ -d "$source_path" ]; then
        echo "   Source: Monorepo ($source_path)"

        # Create symlink instead of copying (for live reload)
        if [ -L "$node_path" ]; then
            echo "   ✅ Symlink already exists"
        elif [ -d "$node_path" ]; then
            echo "   ⚠️  Directory exists, removing..."
            rm -rf "$node_path"
            ln -s "$source_path" "$node_path"
            echo "   ✅ Created symlink"
        else
            ln -s "$source_path" "$node_path"
            echo "   ✅ Created symlink"
        fi

        # Install Python requirements if they exist
        if [ -f "$source_path/requirements.txt" ]; then
            echo "   📋 Installing Python requirements..."
            pip install -q -r "$source_path/requirements.txt"
            echo "   ✅ Requirements installed"
        fi

        # Create .env from template if needed
        if [ -f "$source_path/.env.example" ] && [ ! -f "$source_path/.env" ]; then
            echo "   📝 Creating .env from .env.example..."
            cp "$source_path/.env.example" "$source_path/.env"
            echo "   ⚠️  Please edit $source_path/.env with your API keys"
        fi
    else
        echo "   ❌ Not found in monorepo: $source_path"
    fi

    echo ""
}

# Read config_nodes.json and install each node
CONFIG_FILE="$MONOREPO_CUSTOM_NODES/config_nodes.json"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ Error: config_nodes.json not found at $CONFIG_FILE"
    exit 1
fi

echo "📋 Reading custom nodes configuration from config_nodes.json"
echo ""

# Parse JSON and extract node names (simple approach using grep/sed)
# This assumes config_nodes.json has a "nodes" array
NODE_NAMES=$(cat "$CONFIG_FILE" | grep -o '"[^"]*"' | grep -v "url\|branch\|requirements\|recursive\|env" | sed 's/"//g' | sort -u)

# Install each node
for node_name in $NODE_NAMES; do
    # Skip JSON keys and empty strings
    if [[ "$node_name" =~ ^(nodes|url|branch|requirements|recursive|env)$ ]] || [ -z "$node_name" ]; then
        continue
    fi

    install_node "$node_name"
done

echo "════════════════════════════════════════════════════════════"
echo "✅ Custom Nodes Installation Complete!"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "📝 Next steps:"
echo "   1. Check .env files in custom nodes that need API keys"
echo "   2. Set GEMINI_API_KEY: export GEMINI_API_KEY='your-key'"
echo "   3. Run ComfyUI: python3 main.py --listen 0.0.0.0 --port 8188"
echo ""
