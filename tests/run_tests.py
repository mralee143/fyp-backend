#!/usr/bin/env python
"""
Script to run comprehensive test suite and generate a summary.
"""
import subprocess
import sys

def run_tests():
    """Run pytest and capture results."""
    cmd = [
        sys.executable, "-m", "pytest",
        "--tb=line",
        "--maxfail=50",
        "-v",
        "--timeout=300"
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600
        )
        print(result.stdout)
        print(result.stderr)
        return result.returncode
    except subprocess.TimeoutExpired:
        print("Tests timed out after 600 seconds")
        return 1
    except Exception as e:
        print(f"Error running tests: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(run_tests())
