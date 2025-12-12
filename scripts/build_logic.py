import os
import sys
import json
import requests
import re
import zipfile
import shutil
import subprocess
import time
from bs4 import BeautifulSoup

# --- Configuration ---
# Headers to mimic a browser
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1"
}

def log(msg):
    print(f"[+] {msg}", flush=True)

def error(msg):
    print(f"[!] {msg}", flush=True)
    sys.exit(1)

def download_file(url, filename):
    log(f"Downloading {url} -> {filename}")
    try:
        with requests.get(url, stream=True, headers=HEADERS) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return filename
    except Exception as e:
        error(f"Download failed: {e}")

def get_latest_github_release(repo):
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        resp = requests.get(url, headers=HEADERS).json()
        return resp
    except Exception as e:
        error(f"Failed to fetch release for {repo}: {e}")

def fetch_revanced_tools():
    """Downloads the latest CLI and Patches (RVP) from official repos."""
    os.makedirs("tools", exist_ok=True)
    
    # 1. Fetch CLI
    cli_release = get_latest_github_release("ReVanced/revanced-cli")
    cli_asset = next(a for a in cli_release['assets'] if a['name'].endswith('.jar'))
    cli_path = f"tools/{cli_asset['name']}"
    if not os.path.exists(cli_path):
        download_file(cli_asset['browser_download_url'], cli_path)
    
    # 2. Fetch Patches (RVP)
    patches_release = get_latest_github_release("ReVanced/revanced-patches")
    patches_rvp_asset = next(a for a in patches_release['assets'] if a['name'].endswith('.rvp'))
    patches_rvp_path = f"tools/{patches_rvp_asset['name']}"
    if not os.path.exists(patches_rvp_path):
        download_file(patches_rvp_asset['browser_download_url'], patches_rvp_path)
        
    return cli_path, patches_rvp_path

def get_compatible_version(package_name, cli_path, patches_rvp_path, manual_version=None):
    if manual_version and manual_version != "auto":
        log(f"Manual version override: {manual_version}")
        return manual_version

    log(f"Finding compatible version for {package_name} using CLI...")
    
    # Check if we can get list of patches
    cmd = [
        "java", "-jar", cli_path, 
        "list-patches", 
        "--with-packages", "--with-versions", 
        patches_rvp_path
    ]
    
    try:
        result = subprocess.check_output(cmd, text=True)
    except subprocess.CalledProcessError as e:
        error(f"Failed to list patches: {e}")
        return None

    versions = set()
    
    # Regex to find package name followed by versions in parentheses
    # Adapting to potentially varying CLI output formats
    # Typical format: "com.package.name (v1, v2)" or indented under a patch
    
    # Escape dots in package name for regex
    pkg_regex = re.escape(package_name)
    
    # Search line by line
    for line in result.splitlines():
        if package_name in line:
            # Look for version pattern inside parentheses
            # Captures "18.01.32, 18.02.33" from "com.pkg (18.01.32, 18.02.33)"
            # Also handles potential "v" prefix like "v18.01.32"
            matches = re.findall(r'\(([\d\.,\s\w]+)\)', line)
            for match in matches:
                # Split by comma or space
                raw_vs = re.split(r'[,\s]+', match)
                for v in raw_vs:
                    v = v.strip()
                    # Basic semantic version check (digits and dots)
                    if re.match(r'^\d+(\.\d+)+$', v):
                        versions.add(v)

    if not versions:
        log(f"No specific compatible versions found for {package_name} in CLI output.")
        log("Possible reasons: Universal patches only, or parsing mismatch.")
        log("Attempting to find 'Latest' version from APKMirror as fallback...")
        return "latest"

    # Sort versions (numeric sort)
    def version_key(v):
        try:
            return [int(x) for x in v.split('.')]
        except:
            return [0]
    
    sorted_versions = sorted(list(versions), key=version_key, reverse=True)
    best_version = sorted_versions[0]
    log(f"Latest compatible version found: {best_version}")
    return best_version

# --- APKMirror Scraper (Sp3EdeR Logic) ---

def get_soup(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 403:
            log("Hit 403 on APKMirror. Waiting 5s...")
            time.sleep(5)
            resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, 'html.parser')
    except Exception as e:
        log(f"Error fetching {url}: {e}")
        return None

def scrape_apkmirror(app_name, version):
    """
    Downloads APK from APKMirror.
    Flow: Search -> Release Page -> Variant Page (Arm64) -> Download Page -> File
    """
    base_url = "https://www.apkmirror.com"
    
    # 1. Search
    # If version is 'latest', we search just app name
    query = f"{app_name} {version}" if version != "latest" else app_name
    search_url = f"{base_url}/?post_type=app_release&searchtype=apk&s={query.replace(' ', '+')}"
    
    log(f"Searching APKMirror: {search_url}")
    soup = get_soup(search_url)
    if not soup: return None
    
    # Find release row
    release_url = None
    rows = soup.find_all("div", class_="appRow")
    
    for row in rows:
        title_div = row.find("h5", class_="appRowTitle")
        if not title_div: continue
        
        title_text = title_div.get_text().strip()
        link = title_div.find("a")['href']
        
        # If specific version, match it. If latest, take first result.
        if version == "latest":
            release_url = base_url + link
            # Extract actual version from title if possible for logging
            # (Assuming format "App Name Version")
            log(f"Selected latest release: {title_text}")
            # Update global version variable if needed? 
            # Ideally we return the version found too, but for now we just download.
            break
        elif version in title_text:
            release_url = base_url + link
            log(f"Found release page: {release_url}")
            break
            
    if not release_url:
        log("Release not found.")
        return None
        
    # 2. Select Variant
    time.sleep(1)
    soup = get_soup(release_url)
    if not soup: return None
    
    # We need to find the variant table rows
    # Columns usually: Variant, Arch, Min Android, DPI
    variants = soup.find_all("div", class_="table-row")
    
    target_variant_url = None
    
    # Scoring system for variants
    # arm64-v8a > universal > noarch
    # nodpi > specific dpi
    # apk > bundle (if possible)
    
    best_score = -999
    
    for row in variants:
        cells = row.find_all("div", class_="table-cell")
        if len(cells) < 4: continue
        
        # Text extraction
        variant_info = cells[0].get_text().strip().lower()
        arch_info = cells[1].get_text().strip().lower()
        dpi_info = cells[3].get_text().strip().lower()
        
        link_tag = cells[0].find("a", class_="accent_color")
        if not link_tag: continue
        url = base_url + link_tag['href']
        
        score = 0
        
        # ARCHITECTURE (Critical)
        if "arm64-v8a" in arch_info: 
            score += 100
        elif "universal" in arch_info:
            score += 50
        elif "noarch" in arch_info:
            score += 50
        elif "x86" in arch_info:
            score = -1000 # Skip x86
        else:
            score = -100 # unknown/armeabi-v7a only
            
        # DPI
        if "nodpi" in dpi_info:
            score += 20
        
        # TYPE
        if "apk" in variant_info:
            score += 10
        elif "bundle" in variant_info:
            score += 5
            
        if score > best_score:
            best_score = score
            target_variant_url = url
            
    if not target_variant_url or best_score < 0:
        log("No suitable arm64 variant found.")
        return None
        
    log(f"Selected Variant: {target_variant_url}")
    
    # 3. Get Download Link
    time.sleep(1)
    soup = get_soup(target_variant_url)
    if not soup: return None
    
    # Find "Download APK" button link
    # Usually class="accent_bg btn btn-flat downloadButton"
    btn = soup.select_one("a.downloadButton")
    if not btn:
        log("Download button not found on variant page.")
        return None
        
    download_page_url = base_url + btn['href']
    log(f"Download Page: {download_page_url}")
    
    # 4. Final Click
    time.sleep(1)
    soup = get_soup(download_page_url)
    if not soup: return None
    
    # Find "here" link
    # <p class="notes"> ... <a rel="nofollow" href="...">here</a> ... </p>
    here_link = soup.find("a", string=re.compile("here", re.I))
    final_url = None
    
    if here_link:
        final_url = base_url + here_link['href']
    else:
        # Fallback: look for /wp-content/themes/apk-mirror/download.php
        fallback_link = soup.find("a", href=re.compile(r"download\.php\?id="))
        if fallback_link:
            final_url = base_url + fallback_link['href']
            
    if not final_url:
        log("Final download link not found.")
        return None
        
    # Download
    filename = f"downloads/{app_name.replace(' ', '')}-{version}.apk"
    download_file(final_url, filename)
    return filename

# --- Split APK Handling ---

def process_apk(apk_path):
    """
    Handles Bundles (.apkm, .xapk, .zip).
    Extracts base.apk and config.arm64_v8a.apk
    """
    if not zipfile.is_zipfile(apk_path):
        return [apk_path]
        
    log("Bundle detected. Extracting...")
    extract_dir = "extracted_apk"
    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir)
    os.makedirs(extract_dir)
    
    with zipfile.ZipFile(apk_path, 'r') as z:
        z.extractall(extract_dir)
        
    # Search for apks
    files = []
    for root, _, filenames in os.walk(extract_dir):
        for f in filenames:
            if not f.endswith(".apk"): continue
            full_path = os.path.join(root, f)
            
            # Logic: We need base + arm64
            # We reject x86
            
            if "x86" in f: continue
            
            # Usually keep base.apk, and anything with arm64
            # Or if it's a split config, keep density splits (xhdpi etc) if unsure, 
            # but usually revanced needs base + architecture.
            files.append(full_path)
            
    log(f"Files to patch: {files}")
    return files

# --- Main ---

def main():
    package_name = os.environ.get("PACKAGE_NAME")
    app_name = os.environ.get("APP_NAME")
    manual_version = os.environ.get("VERSION", "auto")
    
    if not package_name or not app_name:
        error("Missing env vars")
        
    cli_path, patches_rvp_path = fetch_revanced_tools()
    
    # Determine Version
    target_version = get_compatible_version(package_name, cli_path, patches_rvp_path, manual_version)
    
    if not target_version:
        error("Could not determine version and fallback failed.")
        
    # Download
    os.makedirs("downloads", exist_ok=True)
    apk_path = scrape_apkmirror(app_name, target_version)
    
    if not apk_path:
        # Fallback to APKPure logic if desired, or fail
        error("Download failed from APKMirror")
        
    # Process
    input_files = process_apk(apk_path)
    
    # Patch
    output_apk = f"build/{app_name.replace(' ', '')}-ReVanced-v{target_version}.apk"
    os.makedirs("build", exist_ok=True)
    
    cmd = [
        "java", "-jar", cli_path,
        "patch",
        "-p", patches_rvp_path,
        "-o", output_apk,
    ]
    cmd.extend(input_files)
    
    log(f"Running Patcher...")
    try:
        subprocess.run(cmd, check=True)
        log(f"Success: {output_apk}")
        with open(os.environ['GITHUB_ENV'], 'a') as f:
            f.write(f"PATCHED_APK={output_apk}\n")
            f.write(f"APP_VERSION={target_version}\n")
    except subprocess.CalledProcessError:
        error("Patching failed")

if __name__ == "__main__":
    main()
