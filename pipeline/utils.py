import os

def find_file(directory, contains):
    """Find a file in `directory` whose name contains `contains` (case-insensitive)."""
    for fname in os.listdir(directory):
        if contains.lower() in fname.lower():
            return os.path.join(directory, fname)
    raise FileNotFoundError(f"No file containing '{contains}' found in {directory}")