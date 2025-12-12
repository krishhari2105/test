import os
import requests
import subprocess
import sys

# --- Configuration ---
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
    
    print(f"{'Source':<12} | {'App Package':<40} | {'Recommended Version'}")
    print("-" * 85)

    for source_name, config in SOURCES.items():
        cli_path = download_asset(config["cli_repo"], config["cli_asset"], "tools_check")
        patches_path = download_asset(config["patches_repo"], config["patches_asset"], "tools_check")

        if not cli_path or not patches_path:
            print(f"{source_name:<12} | ERROR: Could not download tools")
            continue

        try:
            # Run list-versions
            cmd = ["java", "-jar", cli_path, "list-versions", patches_path]
            process = subprocess.run(cmd, capture_output=True, text=True)
            
            if process.returncode != 0:
                print(f"{source_name:<12} | CLI Error (Code {process.returncode})")
                print(process.stderr)
                continue

            output = process.stdout
            
            # --- Strict Parsing Logic ---
            found_versions = {}
            current_pkg = None

            for line in output.splitlines():
                if not line.strip(): continue

                # Package lines usually start with NO whitespace
                if not line.startswith(" ") and not line.startswith("\t"):
                    current_pkg = line.strip()
                    continue
                
                # Version lines are indented
                if current_pkg and (line.startswith(" ") or line.startswith("\t")):
                    v = line.strip()
                    # Filter out non-version strings (sometimes output has other info)
                    # We assume a version contains at least one dot and starts with a digit or 'v'
                    if "." in v and (v[0].isdigit() or v.startswith('v')):
                         if current_pkg not in found_versions:
                             found_versions[current_pkg] = []
                         found_versions[current_pkg].append(v)

            # --- Display Results ---
            for app in APPS_TO_CHECK:
                if app in found_versions:
                    # Sort versions desc (assuming standard semantic versioning)
                    vs = found_versions[app]
                    # Cleanup 'v' for sorting
                    def sort_key(s):
                        try:
                            return [int(x) for x in s.lstrip('v').split('.')]
                        except:
                            return [0]
                    
                    vs.sort(key=sort_key, reverse=True)
                    latest = vs[0]
                    print(f"{source_name:<12} | {app:<40} | {latest}")
                else:
                    print(f"{source_name:<12} | {app:<40} | None (Not in patches)")
            
            # Debug dump if YouTube wasn't found (implies parsing failure or format change)
            if "com.google.android.youtube" not in found_versions:
                print(f"\n[DEBUG] Raw Output for {source_name} (First 20 lines):")
                print("\n".join(output.splitlines()[:20]))
                print("-" * 20 + "\n")

        except Exception as e:
            print(f"{source_name:<12} | Exception: {e}")

if __name__ == "__main__":
    check_versions()
