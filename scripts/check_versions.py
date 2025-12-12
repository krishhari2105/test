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
            # Use --with-versions to get version info
            cmd = ["java", "-jar", cli_path, "list-patches", "--with-packages", "--with-versions", patches_path]
            output = subprocess.check_output(cmd, text=True)
            
            # --- DEBUG: Print first few lines of output to see format ---
            # print(f"DEBUG OUTPUT FOR {source_name}:")
            # print(output[:500]) 
            # ------------------------------------------------------------

        except Exception as e:
            print(f"{source_name:<15} | CLI Error: {e}")
            continue

        for app in APPS_TO_CHECK:
            versions = set()
            
            # Regex strategy:
            # 1. Look for lines like "com.package.name (v1, v2)"
            # 2. Look for lines that might be indented under the package
            
            # This regex captures versions in parentheses immediately following the package name
            # e.g. "com.google.android.youtube (19.04.37, 19.05.36)"
            # It also handles cases with spaces or "v" prefixes
            
            # Escape the app package name for regex safety
            escaped_app = re.escape(app)
            
            # Pattern: app_package followed by versions in parentheses
            pattern = rf"{escaped_app}\s*\((.*?)\)"
            
            matches = re.findall(pattern, output)
            for match in matches:
                # 'match' is the content inside parens, e.g. "19.04.37, 19.05.36"
                raw_vs = re.split(r'[,\s]+', match)
                for v in raw_vs:
                    clean_v = v.strip()
                    # Filter for something that looks like a version (digits.digits)
                    if re.match(r'^\d+(\.\d+)+$', clean_v):
                        versions.add(clean_v)

            if versions:
                # Sort by version number logic (not string)
                def version_sort_key(v):
                    try:
                        return [int(part) for part in v.split('.')]
                    except:
                        return [0]

                sorted_vs = sorted(list(versions), key=version_sort_key, reverse=True)
                # Show top result (latest)
                print(f"{source_name:<15} | {app:<40} | {sorted_vs[0]}")
            else:
                print(f"{source_name:<15} | {app:<40} | Any/Universal (or parsing failed)")

if __name__ == "__main__":
    check_versions()
