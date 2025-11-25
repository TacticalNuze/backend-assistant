#!/bin/bash
# Setup script for creating a virtual environment and installing dependencies
# for the RAG evaluation service

set -e

echo "=========================================="
echo "Setting up virtual environment for RAG Evaluation Service"
echo "=========================================="

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
VENV_DIR="$SCRIPT_DIR/venv"

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is not installed or not in PATH"
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment at $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
    echo "✓ Virtual environment created"
else
    echo "Virtual environment already exists at $VENV_DIR"
fi

# Activate virtual environment
echo "Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install requirements
echo "Installing required packages..."
pip install -r "$SCRIPT_DIR/requirements.txt"

echo ""
echo "=========================================="
echo "Setup complete!"
echo "=========================================="
echo ""
echo "To activate the virtual environment, run:"
echo "  source $VENV_DIR/bin/activate"
echo ""
echo "To deactivate, run:"
echo "  deactivate"
echo ""




