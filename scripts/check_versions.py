import os
import requests
import re
import subprocess
import json
import sys

# --- Configuration for Patch Sources ---
SOURCES = {
    "revanced": {
        "patches_repo": "ReVanced/revanced-patches",
        "cli_repo": "ReVanced/revanced-cli",
        "patches_asset": ".rvp",
        "cli_asset": ".jar"
    },
    "inotia00": {
        "patches_repo": "inotia00/revanced-patches",
        "cli_repo": "inotia00/revanced-cli", 
        "patches_asset": ".rvp",
        "cli_asset": ".jar"
    },
    "anddea": {
        "patches_repo": "anddea/revanced-patches",
        "cli_repo": "inotia00/revanced-cli", 
        "patches_asset": ".rvp",
        "cli_asset": ".jar"
    }
}

APPS_TO_CHECK = [
    "com.google.android.youtube",
    "com.google.android.apps.youtube.music",
    "com.reddit.frontpage",
    "com.twitter.android",
    "com.spotify.music"
]

HEADERS = {"User-Agent": "Mozilla/5.0"}

def download_asset(repo, extension, output_dir):
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        resp = requests.get(url, headers=HEADERS).json()
        if 'assets' not in resp:
            print(f"Error: No assets found for {repo}")
            return None
            
        for asset in resp['assets']:
            if asset['name'].endswith(extension) and "source" not in asset['name']:
                if extension == ".jar" and "all" not in asset['name'] and any("all" in a['name'] for a in resp['assets']):
                    continue
                
                download_url = asset['browser_download_url']
                filename = os.path.join(output_dir, asset['name'])
                if not os.path.exists(filename):
                    print(f"Downloading {asset['name']} from {repo}...")
                    with requests.get(download_url, stream=True) as r:
                        r.raise_for_status()
                        with open(filename, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                f.write(chunk)
                return filename
    except Exception as e:
        print(f"Error fetching {repo}: {e}")
    return None

def check_versions():
    os.makedirs("tools_check", exist_ok=True)
    
    print(f"{'Source':<15} | {'App Package':<40} | {'Recommended Version'}")
    print("-" * 80)

    for source_name, config in SOURCES.items():
        cli_path = download_asset(config["cli_repo"], config["cli_asset"], "tools_check")
        patches_path = download_asset(config["patches_repo"], config["patches_asset"], "tools_check")

        if not cli_path or not patches_path:
            print(f"{source_name:<15} | ERROR: Could not download tools")
            continue

        try:
            # We use 'list-versions' which outputs strict lists of versions per app
            cmd = ["java", "-jar", cli_path, "list-versions", patches_path]
            output = subprocess.check_output(cmd, text=True)
        except Exception as e:
            print(f"{source_name:<15} | CLI Error: {e}")
            continue

        # --- Strict Parsing Logic ---
        # Format usually:
        # com.package.name
        #    18.01.32
        #    18.01.33
        #
        # Logic: Line starting with NO spaces is a package. Line STARTING with spaces is a version.
        
        found_versions = {app: set() for app in APPS_TO_CHECK}
        current_package = None

        for line in output.splitlines():
            if not line.strip(): continue

            # Check indentation to determine hierarchy
            if not line.startswith(" ") and not line.startswith("\t"):
                # This is a Header (Package Name)
                clean_line = line.strip()
                # Only switch if it's exactly one of our target apps
                # This prevents partial matches or logging noise
                if clean_line in APPS_TO_CHECK:
                    current_package = clean_line
                else:
                    current_package = None # Reset if we hit a package we don't care about
            
            elif current_package:
                # This is an indented line under a target package
                # It should be a version number
                clean_v = line.strip()
                
                # Strict Version Regex: digits.digits.digits (optional v prefix)
                # This ignores "Compatible with..." text
                if re.match(r'^v?\d+(\.\d+)+$', clean_v):
                    found_versions[current_package].add(clean_v)

        # Print Results
        for app in APPS_TO_CHECK:
            versions = found_versions[app]
            if versions:
                # Sort versions numeric descending
                def version_sort_key(v):
                    try:
                        # Remove 'v' if present
                        clean = v.lstrip('v')
                        return [int(part) for part in clean.split('.')]
                    except:
                        return [0]
                
                sorted_vs = sorted(list(versions), key=version_sort_key, reverse=True)
                print(f"{source_name:<15} | {app:<40} | {sorted_vs[0]}")
            else:
                print(f"{source_name:<15} | {app:<40} | Any/Universal (or check failed)")

if __name__ == "__main__":
    check_versions()
