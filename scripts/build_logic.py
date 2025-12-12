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
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://google.com"
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
            total_size = int(r.headers.get('content-length', 0))
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

    # Robust parsing strategy
    versions = set()
    
    # Look for lines that contain the package name
    # Format usually: "com.google.android.youtube (19.01.1, 19.02.2)"
    # Or sometimes indented under a patch name
    
    for line in result.splitlines():
        if package_name in line:
            # Extract everything in parentheses
            match = re.search(r'\(([\d\.,\s]+)\)', line)
            if match:
                v_str = match.group(1)
                found_vs = [v.strip() for v in re.split(r'[,\s]+', v_str) if v.strip()]
                # Basic validation: must look like a version number
                valid_vs = [v for v in found_vs if re.match(r'^\d+(\.\d+)+$', v)]
                versions.update(valid_vs)

    if not versions:
        log("No specific compatible versions found. This might be a 'Universal' patch set or parsing failed.")
        log("Dumping first 20 lines of CLI output for debugging:")
        print('\n'.join(result.splitlines()[:20]))
        return None

    # Sort versions
    def version_key(v):
        return [int(x) for x in v.split('.')]
    
    sorted_versions = sorted(list(versions), key=version_key, reverse=True)
    best_version = sorted_versions[0]
    log(f"Latest compatible version found: {best_version}")
    return best_version

# --- Advanced APKMirror Scraper ---

def get_soup(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, 'html.parser')
    except Exception as e:
        log(f"Request failed: {url} | {e}")
        return None

def scrape_apkmirror_full(app_name, version):
    """
    Full scraping logic for APKMirror:
    1. Search -> 2. Release Page -> 3. Variant Page -> 4. Download Page -> 5. Link
    """
    log(f"Scraping APKMirror for {app_name} v{version}...")
    
    # 1. Search
    # Formatting query to match APKMirror search URL structure
    query = f"{app_name} {version}"
    search_url = f"https://www.apkmirror.com/?post_type=app_release&searchtype=apk&s={query.replace(' ', '+')}"
    
    soup = get_soup(search_url)
    if not soup: return None
    
    # Find the correct release row
    # We look for a row where the text contains the version explicitly
    res = soup.find_all("div", class_="appRow")
    release_url = None
    
    for row in res:
        title_tag = row.find("h5", class_="appRowTitle")
        if title_tag:
            title_text = title_tag.get_text().strip()
            # Strict check: Version must be in title
            if version in title_text:
                link_tag = row.find("a", href=True)
                if link_tag:
                    release_url = "https://www.apkmirror.com" + link_tag['href']
                    log(f"Found Release Page: {release_url}")
                    break
    
    if not release_url:
        log("Release not found in search results.")
        return None

    # 2. Get Variant
    # We prefer 'APK' over 'BUNDLE' if possible, but for split apps we might need bundle.
    # ReVanced CLI handles split APKs if we extract them.
    # We prefer 'arm64-v8a' architecture.
    
    time.sleep(1) # Be polite
    soup = get_soup(release_url)
    if not soup: return None
    
    # Find variants table
    # Styles: "table-row headerFont" -> Look for row containing "APK" or "Bundle"
    variants = soup.find_all("div", class_="table-row")
    
    target_variant_url = None
    
    # Priority: APK > Bundle (if we can help it, but for YT Music usually bundle is fine if handled)
    # Architecture: arm64-v8a > universal > noarch
    
    best_score = -1
    
    for row in variants:
        # Extract info
        cells = row.find_all("div", class_="table-cell")
        if len(cells) < 2: continue
        
        variant_text = row.get_text().lower()
        link = row.find("a", class_="accent_color")
        if not link: continue
        
        url = "https://www.apkmirror.com" + link['href']
        
        score = 0
        if "arm64-v8a" in variant_text: score += 10
        elif "universal" in variant_text: score += 5
        elif "noarch" in variant_text: score += 5
        elif "x86" in variant_text: score = -100 # Skip x86
        
        if "apk" in cells[1].get_text().lower(): score += 2 
        # Bundles are okay but standard APK is easier if available
        
        if score > best_score:
            best_score = score
            target_variant_url = url

    if not target_variant_url:
        log("No suitable variant found (arm64/universal).")
        return None
        
    log(f"Selected Variant: {target_variant_url}")
    
    # 3. Download Page
    time.sleep(1)
    soup = get_soup(target_variant_url)
    if not soup: return None
    
    # Look for "Download APK" or "Download Bundle" button
    # usually class="accent_bg btn btn-flat downloadButton"
    download_btn = soup.find("a", class_="downloadButton")
    if not download_btn:
        # sometimes it says "Download APK Bundle"
        download_btn = soup.select_one(".downloadButton")
        
    if not download_btn:
        log("Download button not found on variant page.")
        return None
        
    final_page_url = "https://www.apkmirror.com" + download_btn['href']
    log(f"Navigating to final download page: {final_page_url}")
    
    # 4. Final Link
    time.sleep(1)
    soup = get_soup(final_page_url)
    if not soup: return None
    
    # Looking for: <a rel="nofollow" href="...">here</a>
    # usually inside a p class="notes" or just a direct link saying "here"
    
    direct_link = None
    here_link = soup.find("a", string=re.compile(r"here", re.I))
    
    if here_link:
        direct_link = "https://www.apkmirror.com" + here_link['href']
    else:
        # Fallback: finding the click tracking link
        # often /wp-content/themes/apk-mirror/download.php?id=...
        log("Could not find 'here' link. Trying alternate selector.")
        # Sometimes Cloudflare protects this specific part heavily.
        return None

    if direct_link:
        log(f"Direct Link Found: {direct_link}")
        filename = f"downloads/{app_name.replace(' ','')}-{version}.apk" # defaulting extension, zip check later
        download_file(direct_link, filename)
        return filename
        
    return None

# --- Main Logic ---

def process_apk(apk_path):
    """
    Handles Bundles (.apkm, .xapk, .zip) or Split APKs.
    Extracts and returns list of files to pass to patcher.
    """
    if not apk_path or not os.path.exists(apk_path):
        error("APK path invalid")

    # Check if it's actually a zip/bundle despite .apk extension
    is_zip = False
    try:
        if zipfile.is_zipfile(apk_path):
            # It might be a regular APK (which is a zip) or a Bundle (zip of apks)
            # We check contents to distinguish
            with zipfile.ZipFile(apk_path, 'r') as z:
                contents = z.namelist()
                # If it contains .apk files inside, it's a bundle
                if any(f.endswith('.apk') for f in contents):
                    is_zip = True
    except:
        pass

    if not is_zip:
        return [apk_path]
    
    log("Detected Bundle/Split-APK. Extracting...")
    extract_dir = "extracted_apk"
    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir)
    os.makedirs(extract_dir)
    
    with zipfile.ZipFile(apk_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)
        
    files_to_patch = []
    
    # Logic for common Bundle formats (APKM, XAPK)
    # usually: base.apk + split_config.arch.apk + split_config.dpi.apk
    
    for root, dirs, filenames in os.walk(extract_dir):
        for f in filenames:
            if not f.endswith(".apk"): continue
            
            # Filter logic
            # We WANT: base.apk, arm64_v8a, English language (or no language)
            # We HATE: x86, armeabi-v7a (if we have arm64), specific dpi if we want universal (but dpi splits are usually needed)
            
            if "x86" in f or "armeabi_v7a" in f:
                continue
                
            # If we see arm64, we take it.
            # If we see base, we take it.
            files_to_patch.append(os.path.join(root, f))
            
    if not files_to_patch:
        error("No suitable APKs found in bundle.")
        
    log(f"Selected split files: {files_to_patch}")
    return files_to_patch

def main():
    package_name = os.environ.get("PACKAGE_NAME")
    app_name = os.environ.get("APP_NAME")
    manual_version = os.environ.get("VERSION", "auto")
    
    if not package_name or not app_name:
        error("PACKAGE_NAME or APP_NAME env vars missing")

    # 1. Setup Tools
    cli_path, patches_rvp_path = fetch_revanced_tools()
    
    # 2. Determine Version
    target_version = get_compatible_version(package_name, cli_path, patches_rvp_path, manual_version)
    if not target_version:
        error("Could not determine target version.")
        
    # 3. Download
    # Try APKMirror first as it's the "Gold Standard" for specific versions
    os.makedirs("downloads", exist_ok=True)
    raw_apk_path = scrape_apkmirror_full(app_name, target_version)
    
    if not raw_apk_path:
        log("APKMirror failed. Trying APKPure fallback (less reliable for specific versions)...")
        # Placeholder for APKPure logic if you want to keep the old simple one as backup
        # raw_apk_path = scrape_apkpure(...)
        pass

    if not raw_apk_path:
        error(f"Could not download APK for {app_name} v{target_version}")
        
    # 4. Prepare inputs (Handle Splits)
    input_files = process_apk(raw_apk_path)
    
    # 5. Patch
    output_apk = f"build/{app_name.replace(' ', '-')}-ReVanced-v{target_version}.apk"
    os.makedirs("build", exist_ok=True)
    
    # Command construction
    cmd = [
        "java", "-jar", cli_path,
        "patch",
        "-p", patches_rvp_path,
        "-o", output_apk,
    ]
    
    # For split APKs, the CLI accepts multiple input files directly
    # java -jar cli.jar patch -p patches.rvp -o out.apk base.apk split1.apk split2.apk
    cmd.extend(input_files)
    
    log(f"Running Patcher: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
        log(f"Patching successful! Output: {output_apk}")
        
        with open(os.environ['GITHUB_ENV'], 'a') as f:
            f.write(f"PATCHED_APK={output_apk}\n")
            f.write(f"APP_VERSION={target_version}\n")
            
    except subprocess.CalledProcessError as e:
        error(f"Patching failed: {e}")

if __name__ == "__main__":
    main()
