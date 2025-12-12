import os
import requests
import re
import subprocess
import sys
import zipfile
import shutil
from datetime import datetime

# --- Configuration ---
APK_REPO_OWNER = "krishhari2105"
APK_REPO_NAME = "base-apks"

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
    },
    "lisouseinaikyrios": {
        "patches_repo": "LisoUseInAIKyrios/revanced-patches",
        "cli_repo": "LisoUseInAIKyrios/revanced-cli",
        "patches_asset": ".rvp",
        "cli_asset": ".jar"
    }
}

PKG_MAP = {
    "youtube": "com.google.android.youtube",
    "yt-music": "com.google.android.apps.youtube.music",
    "reddit": "com.reddit.frontpage",
    "twitter": "com.twitter.android",
    "spotify": "com.spotify.music"
}

def log(msg):
    print(f"[+] {msg}", flush=True)

def error(msg):
    print(f"[!] {msg}", flush=True)
    raise Exception(msg)

def download_file(url, filename):
    log(f"Downloading {url} -> {filename}")
    try:
        with requests.get(url, stream=True) as r:
            if r.status_code == 404: return False
            r.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return True
    except Exception as e:
        log(f"Download failed: {e}")
        return False

def get_latest_github_release(repo):
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        resp = requests.get(url).json()
        return resp
    except Exception as e:
        log(f"Failed to fetch release for {repo}: {e}")
        return None

def fetch_tools(source_key):
    config = SOURCES.get(source_key)
    os.makedirs("tools", exist_ok=True)
    
    def get_asset(repo, ext):
        release = get_latest_github_release(repo)
        if not release: return None, None
        
        for asset in release.get('assets', []):
            if asset['name'].endswith(ext) and "source" not in asset['name']:
                if ext == ".jar" and "all" not in asset['name'] and any("all" in a['name'] for a in release['assets']):
                    continue
                return asset['browser_download_url'], asset['name']
        return None, None

    cli_url, cli_name = get_asset(config['cli_repo'], config['cli_asset'])
    if not cli_url: raise Exception(f"Could not find CLI for {source_key}")
    
    cli_path = f"tools/{cli_name}"
    if not os.path.exists(cli_path): download_file(cli_url, cli_path)
    
    patches_url, patches_name = get_asset(config['patches_repo'], config['patches_asset'])
    if not patches_url: raise Exception(f"Could not find Patches for {source_key}")
    
    patches_path = f"tools/{patches_name}"
    if not os.path.exists(patches_path): download_file(patches_url, patches_path)
    
    return cli_path, patches_path

def fetch_apkeditor():
    os.makedirs("tools", exist_ok=True)
    apkeditor_path = "tools/APKEditor.jar"
    if not os.path.exists(apkeditor_path):
        url = "https://github.com/REAndroid/APKEditor/releases/download/V1.4.0/APKEditor-1.4.0.jar"
        if not download_file(url, apkeditor_path):
             raise Exception("Failed to download APKEditor.jar")
    return apkeditor_path

def get_target_version(cli_path, patches_path, package_name, manual_version):
    if manual_version and manual_version != "auto":
        log(f"Manual version override: {manual_version}")
        return manual_version
        
    log(f"Auto-detecting version for {package_name}...")
    cmd = ["java", "-jar", cli_path, "list-versions", patches_path]
    try:
        output = subprocess.check_output(cmd, text=True)
        versions = []
        current_pkg = None
        
        for line in output.splitlines():
            line = line.strip()
            if not line: continue
            
            pkg_match = re.search(r"Package name:\s*([a-zA-Z0-9_.]+)", line)
            if pkg_match:
                current_pkg = pkg_match.group(1)
                continue
                
            if current_pkg == package_name:
                 v_match = re.match(r'^(v?\d+(\.\d+)+)', line)
                 if v_match:
                     versions.append(v_match.group(1))

        if versions:
            versions.sort(key=lambda s: [int(x) for x in s.lstrip('v').split('.') if x.isdigit()], reverse=True)
            log(f"Detected latest compatible version: {versions[0]}")
            return versions[0]
            
    except Exception as e:
        log(f"Error detecting version: {e}")
        
    raise Exception(f"Could not determine version automatically for {package_name}")

def strip_monolithic_apk(apk_path):
    """
    Removes non-arm64 libraries from a monolithic APK to reduce size.
    """
    log(f"Inspecting monolithic APK: {apk_path}")
    
    # Check if it contains multiple architectures
    has_arm64 = False
    has_others = False
    
    try:
        with zipfile.ZipFile(apk_path, 'r') as z:
            for name in z.namelist():
                if "lib/arm64-v8a" in name:
                    has_arm64 = True
                elif "lib/x86" in name or "lib/armeabi-v7a" in name:
                    has_others = True
    except:
        return apk_path # Invalid zip, let patcher fail later

    if not has_arm64:
        log("No arm64-v8a libs found or purely Java/Kotlin app. Skipping strip.")
        return apk_path
        
    if not has_others:
        log("APK is already arm64-only. Skipping strip.")
        return apk_path

    log("Stripping non-arm64 architectures...")
    stripped_path = apk_path.replace(".apk", "_arm64.apk")
    
    with zipfile.ZipFile(apk_path, 'r') as zin:
        with zipfile.ZipFile(stripped_path, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                name = item.filename
                # Filter: Keep if NOT lib OR (is lib AND is arm64)
                if not name.startswith("lib/") or "lib/arm64-v8a/" in name:
                    buffer = zin.read(name)
                    zout.writestr(item, buffer)
                    
    log(f"Stripped APK created: {stripped_path}")
    return stripped_path

def merge_bundle(bundle_path, apkeditor_path):
    """
    Extracts bundle and merges ONLY arm64 splits using APKEditor.
    """
    log(f"Processing bundle for arm64 extraction: {bundle_path}")
    extract_dir = f"extracted_{os.path.basename(bundle_path)}"
    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir)
    os.makedirs(extract_dir)
    
    try:
        with zipfile.ZipFile(bundle_path, 'r') as z:
            z.extractall(extract_dir)
    except zipfile.BadZipFile:
        raise Exception("Downloaded file is not a valid zip/apkm file.")

    # Filter splits inside extracted directory
    # We delete anything that is architecture specific BUT NOT arm64
    # Common names: split_config.arm64_v8a.apk, split_config.armeabi_v7a.apk
    
    for root, dirs, files in os.walk(extract_dir):
        for f in files:
            if not f.endswith(".apk"): continue
            
            # Logic: If it specifies an arch, it MUST be arm64
            if "x86" in f or "armeabi" in f or "mips" in f:
                if "arm64" not in f:
                    log(f"Removing unwanted split: {f}")
                    os.remove(os.path.join(root, f))
    
    output_merged = bundle_path.replace(".apkm", "_arm64.apk").replace(".apks", "_arm64.apk").replace(".xapk", "_arm64.apk")
    if output_merged == bundle_path:
        output_merged = bundle_path + "_merged_arm64.apk"

    log(f"Merging filtered splits into: {output_merged}")
    
    cmd = [
        "java", "-jar", apkeditor_path,
        "m", "-i", extract_dir, "-o", output_merged
    ]
    
    try:
        subprocess.run(cmd, check=True)
        log("Merge successful.")
        shutil.rmtree(extract_dir) # cleanup
        return output_merged
    except subprocess.CalledProcessError as e:
        raise Exception(f"Failed to merge APK bundle: {e}")

def find_apk_in_release(app_name, version):
    log(f"Searching release assets for {app_name} v{version}...")
    release = get_latest_github_release(f"{APK_REPO_OWNER}/{APK_REPO_NAME}")
    if not release: raise Exception("Could not fetch APK repo releases.")
    
    target_base = f"{app_name}-v{version}"
    for asset in release.get('assets', []):
        name = asset['name']
        # Strict match on start
        if name.startswith(target_base) and name.endswith(('.apk', '.apkm', '.apks', '.xapk')):
            return asset['browser_download_url'], name
    return None, None

def patch_app(app_key, patch_source, version_override, cli_path, patches_path):
    pkg = PKG_MAP.get(app_key)
    if not pkg: 
        log(f"Skipping {app_key}: Unknown package map")
        return False

    try:
        version = get_target_version(cli_path, patches_path, pkg, version_override)
        
        # Download
        dl_url, apk_filename = find_apk_in_release(app_key, version)
        if not dl_url:
            log(f"SKIP: APK {app_key}-v{version} not found in storage repo.")
            return False
            
        os.makedirs("downloads", exist_ok=True)
        local_apk = f"downloads/{apk_filename}"
        if not download_file(dl_url, local_apk):
             raise Exception("Download failed")

        # Process Input (Bundle Merge OR Monolithic Strip)
        final_apk_path = local_apk
        
        if local_apk.endswith((".apkm", ".apks", ".xapk")):
            apkeditor_path = fetch_apkeditor()
            final_apk_path = merge_bundle(local_apk, apkeditor_path)
        else:
            # It's a standard APK, try to strip it
            final_apk_path = strip_monolithic_apk(local_apk)

        # Patch
        dist_dir = "dist"
        os.makedirs(dist_dir, exist_ok=True)
        out_apk = f"{dist_dir}/{app_key}-{patch_source}-v{version}-arm64.apk"
        
        cmd = [
            "java", "-jar", cli_path,
            "patch",
            "-p", patches_path,
            "-o", out_apk,
            final_apk_path
        ]
        
        log(f"Patching {app_key}...")
        subprocess.run(cmd, check=True)
        log(f"Successfully created {out_apk}")
        return True

    except Exception as e:
        log(f"FAILED processing {app_key}: {e}")
        return False

def main():
    patch_source = os.environ.get("PATCH_SOURCE")
    apps_input = os.environ.get("APPS_LIST", "all")
    manual_version = os.environ.get("VERSION", "auto")

    if not patch_source: 
        print("[!] PATCH_SOURCE env var missing")
        sys.exit(1)

    if apps_input.lower() == "all":
        apps_to_process = list(PKG_MAP.keys())
    else:
        apps_to_process = [x.strip() for x in apps_input.split(",") if x.strip()]

    log(f"Batch Processing: {apps_to_process} using {patch_source}")

    try:
        cli_path, patches_path = fetch_tools(patch_source)
    except Exception as e:
        print(f"[!] Critical: Tool fetch failed - {e}")
        sys.exit(1)

    success_count = 0
    for app in apps_to_process:
        print("\n" + "="*40)
        log(f"Starting {app}...")
        if patch_app(app, patch_source, manual_version, cli_path, patches_path):
            success_count += 1
            
    print("\n" + "="*40)
    log(f"Batch completed. Successful builds: {success_count}/{len(apps_to_process)}")
    
    date_str = datetime.now().strftime("%Y.%m.%d")
    tag_name = f"v{date_str}-{patch_source}"
    with open(os.environ['GITHUB_ENV'], 'a') as f:
        f.write(f"RELEASE_TAG={tag_name}\n")
        f.write(f"RELEASE_NAME=ReVanced {patch_source.capitalize()} - {date_str}\n")
    
    if success_count == 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
