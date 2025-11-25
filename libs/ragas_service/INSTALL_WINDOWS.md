# Installing Ragas on Windows

## Problem
The `ragas` package depends on `scikit-network`, which requires Microsoft Visual C++ 14.0 or greater to build from source on Windows.

## Solutions

### Solution 1: Install Microsoft C++ Build Tools (Recommended)

1. **Download and Install Microsoft C++ Build Tools:**
   - Visit: https://visualstudio.microsoft.com/visual-cpp-build-tools/
   - Download "Build Tools for Visual Studio"
   - Run the installer
   - Select "C++ build tools" workload
   - Make sure "Windows 10/11 SDK" is checked
   - Click "Install"

2. **After installation, restart your terminal/command prompt**

3. **Install ragas:**
   ```bash
   pip install ragas
   ```

### Solution 2: Use Conda (Alternative)

Conda often has pre-built binaries that don't require compilation:

1. **Install Miniconda or Anaconda** (if not already installed)
   - Download from: https://docs.conda.io/en/latest/miniconda.html

2. **Create a new conda environment:**
   ```bash
   conda create -n ragas_env python=3.11
   conda activate ragas_env
   ```

3. **Install ragas via conda-forge:**
   ```bash
   conda install -c conda-forge ragas
   ```

   Or try installing via pip in the conda environment:
   ```bash
   pip install ragas
   ```

### Solution 3: Use WSL (Windows Subsystem for Linux)

If you have WSL installed, you can install ragas in the Linux environment where compilation is easier:

```bash
wsl
pip install ragas
```

### Solution 4: Try Installing Pre-built Dependencies First

Sometimes installing dependencies separately can help:

```bash
pip install numpy scipy networkx
pip install ragas --no-build-isolation
```

## Verification

After installation, verify it works:

```python
python -c "import ragas; print('Ragas installed successfully!')"
```

## Notes

- The Microsoft C++ Build Tools are free and only need to be installed once
- They're useful for many Python packages that require compilation
- The installation is ~6GB, so ensure you have enough disk space


