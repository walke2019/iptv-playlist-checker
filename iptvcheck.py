# IPTVCheck v1.0
# THIS CODE/WORK IS LICENSED UNDER THE GNU Affero General Public License v3.0 (GNU AGPLv3)
# Copyright (c) 2025 MustardChef
# THE GNU AGPLv3 LICENSE APPLICABLE TO THIS CODE/WORK CAN BE FOUND IN THE LICENSE FILE IN THE ROOT DIRECTORY OF THIS GITHUB REPOSITORY


import os, requests, argparse, logging, concurrent.futures, subprocess, time, signal, sys, threading, urllib3, shutil
from typing import Tuple, Optional
from tqdm import tqdm
from colorama import Fore, Style, init


# Configuration settings
RETRY_COUNT = 1
SKIPPED_FILE_PATH = 'other/skipped.txt'
FFMPEG_TIMEOUT = 25
NUM_THREADS = 4  # Change to the required number of threads

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Initializing colorama for Windows and Linux
init(autoreset=True)

# Logging for debug
logging.basicConfig(filename='iptv_check.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# Channel statistics using the class
class Stats:
    def __init__(self):
        self.working = 0
        self.failed = 0
        self.timeout = 0
        self.skipped = 0

    def reset(self):
        """Сброс статистики для нового запуска."""
        self.working = 0
        self.failed = 0
        self.timeout = 0
        self.skipped = 0

    def log_summary(self):
        total = self.working + self.failed + self.timeout + self.skipped
        logging.info("=== Summary ===")
        logging.info(f"Total channels: {total}")
        if total > 0:
            logging.info(f"Working: {self.working} ({self.working / total * 100:.2f}%)")
            logging.info(f"Failed: {self.failed} ({self.failed / total * 100:.2f}%)")
            logging.info(f"Timeouts: {self.timeout} ({self.timeout / total * 100:.2f}%)")
            logging.info(f"Skipped: {self.skipped} ({self.skipped / total * 100:.2f}%)")
        else:
            logging.info("No channels processed.")

    def print_summary(self):
        total = self.working + self.failed + self.timeout + self.skipped
        print(f"\n{Fore.YELLOW}=== Statistics ==={Style.RESET_ALL}")
        print(
            f"{Fore.GREEN}Working channels added: {self.working} ({self.working / total * 100:.2f}%)" if total > 0 else "No channels processed.")
        print(
            f"{Fore.RED}Failed channels removed: {self.failed} ({self.failed / total * 100:.2f}%)" if total > 0 else "")
        print(f"{Fore.BLUE}Timeouts: {self.timeout} ({self.timeout / total * 100:.2f}%)" if total > 0 else "")
        print(
            f"{Fore.YELLOW}Skipped channels: {self.skipped} ({self.skipped / total * 100:.2f}%){Style.RESET_ALL}" if total > 0 else "")


stats = Stats()
os.makedirs(os.path.dirname(SKIPPED_FILE_PATH), exist_ok=True)
lock = threading.Lock()


def signal_handler(sig, frame):
    print("\nGracefully shutting down...")
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)


def check_dependencies():
    """Checking for dependencies such as ffmpeg and requests."""
    ffmpeg_path = shutil.which('ffmpeg')

    if ffmpeg_path is None:
        print(f"{Fore.RED}ffmpeg is not installed or not found in PATH!{Style.RESET_ALL}")
        sys.exit(1)
    else:
        try:
            subprocess.run([ffmpeg_path, '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            print(f"{Fore.GREEN}ffmpeg found in the path: {ffmpeg_path}{Style.RESET_ALL}")
        except subprocess.CalledProcessError:
            print(f"{Fore.RED}Error when trying to execute ffmpeg!{Style.RESET_ALL}")
            sys.exit(1)

    try:
        import requests
    except ImportError:
        print(f"{Fore.RED}The package is not installed!{Style.RESET_ALL}")
        sys.exit(1)


# Cache for storing test results
cache = {}


def parse_playlist(content: str) -> list:
    """Parse playlist content into a structured format handling various M3U extensions."""
    lines = content.splitlines()
    channels = []
    current_channel = None
    
    for line in lines:
        line = line.strip()
        
        if not line or line == "#EXTM3U":
            continue
            
        if line.startswith("#EXTINF:"):
            # Start a new channel entry
            current_channel = {
                'extinf': line,
                'url': None,
                'options': []
            }
            channels.append(current_channel)
            
        elif line.startswith(("#EXTVLCOPT:", "#KODIPROP:", "#EXTGRP:", "#EXTLOGO:")):
            # Store additional directives
            if current_channel:
                current_channel['options'].append(line)
                
        elif line.startswith("http://") or line.startswith("https://") or line.startswith("rtmp://") or \
             line.startswith("rtsp://") or line.startswith("mms://") or line.startswith("udp://"):
            # This is a URL
            if current_channel:
                current_channel['url'] = line
    
    # Filter out channels without URLs
    return [ch for ch in channels if ch['url']]


def check_stream(url: str, channel_name: str, headers: Optional[dict] = None, ffmpeg_timeout: int = FFMPEG_TIMEOUT) -> Tuple[bool, Optional[str]]:
    """Validate stream against URL using ffmpeg and HTTP request. Returns a tuple (success, error) for logging."""
    if url in cache:
        return cache[url]

    for attempt in range(RETRY_COUNT + 1):
        try:
            logging.debug(f"Checking stream: {channel_name} ({url}) with headers: {headers}) - Attempt {attempt + 1}")

            # First check with direct HTTP request
            http_success = False
            if url.startswith('http://') or url.startswith('https://'):
                try:
                    response = requests.head(url, headers=headers, timeout=15, verify=False)
                    if response.status_code == 200:
                        http_success = True
                    else:
                        # Some IPTV servers don't support HEAD requests, try GET instead
                        response = requests.get(url, headers=headers, timeout=15, verify=False, stream=True)
                        response.close()  # Close stream immediately to avoid downloading too much data
                        if response.status_code == 200:
                            http_success = True
                        else:
                            logging.warning(f"HTTP request failed for {channel_name} with status code: {response.status_code}")
                except requests.RequestException as e:
                    logging.warning(f"HTTP request failed for {channel_name}: {e}")
            
            # If HTTP check passed, try a more lenient ffmpeg test
            if http_success:
                ffmpeg_timeout_reduced = min(ffmpeg_timeout, 10)  # Reduce timeout for quicker checks when HTTP succeeded
            else:
                ffmpeg_timeout_reduced = ffmpeg_timeout

            # Build ffmpeg command with all necessary headers
            ffmpeg_command = ['ffmpeg']
            
            # Add headers to ffmpeg correctly
            if headers:
                # Add user agent if present
                if 'User-Agent' in headers:
                    ffmpeg_command.extend(['-user_agent', headers['User-Agent']])
                
                # Build headers string for other headers
                header_str = ""
                for key, value in headers.items():
                    if key not in ['User-Agent'] and key and value:  # Include Referer in header string
                        # Convert header name to Title-Case for consistency
                        formatted_key = '-'.join(word.capitalize() for word in key.split('-'))
                        header_str += f"{formatted_key}: {value}\r\n"
                
                # Only add headers parameter if we have headers to add
                if header_str:
                    ffmpeg_command.extend(['-headers', header_str])
            
            # Add quiet mode to reduce noise and add stream URL
            ffmpeg_command.extend([
                '-loglevel', 'warning',
                '-reconnect', '1',
                '-reconnect_streamed', '1',
                '-reconnect_delay_max', '5',
                '-i', url,
                '-t', '3',  # Just check first 3 seconds
                '-f', 'null', 
                '-'
            ])
            
            logging.debug(f"Running ffmpeg command: {' '.join(ffmpeg_command)}")
            result = subprocess.run(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=ffmpeg_timeout_reduced)
            
            if result.returncode == 0:
                stats.working += 1
                cache[url] = (True, None)
                return True, None
                
            # First attempt failed - let's try alternative approaches
            error_output = result.stderr.decode('utf-8', errors='ignore')
            logging.debug(f"ffmpeg error output: {error_output}")
            
            # Try with ffprobe instead - sometimes more lenient
            if attempt == 0:
                try:
                    ffprobe_cmd = ['ffprobe']
                    
                    # Add user agent if present
                    if headers and 'User-Agent' in headers:
                        ffprobe_cmd.extend(['-user_agent', headers['User-Agent']])
                    
                    # Add headers
                    if headers:
                        header_str = ""
                        for key, value in headers.items():
                            if key != 'User-Agent' and key and value:
                                formatted_key = '-'.join(word.capitalize() for word in key.split('-'))
                                header_str += f"{formatted_key}: {value}\r\n"
                        
                        if header_str:
                            ffprobe_cmd.extend(['-headers', header_str])
                    
                    ffprobe_cmd.extend([
                        '-v', 'error',
                        '-show_entries', 'format=duration',
                        '-of', 'default=noprint_wrappers=1:nokey=1',
                        '-i', url
                    ])
                    
                    probe_result = subprocess.run(ffprobe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=ffmpeg_timeout_reduced)
                    if probe_result.returncode == 0 or (probe_result.stdout and float(probe_result.stdout.strip() or 0) > 0):
                        stats.working += 1
                        cache[url] = (True, None)
                        return True, None
                except Exception as e:
                    logging.debug(f"ffprobe check failed: {e}")
                
                # Try curl as a last resort for HTTP streams
                if url.startswith(('http://', 'https://')) and headers:
                    try:
                        curl_cmd = ['curl', '-s', '-I', '-L']
                        
                        # Add headers to curl
                        for key, value in headers.items():
                            curl_cmd.extend(['-H', f"{key}: {value}"])
                        
                        curl_cmd.append(url)
                        curl_result = subprocess.run(curl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                        
                        if curl_result.returncode == 0 and b"200 OK" in curl_result.stdout:
                            stats.working += 1
                            cache[url] = (True, None)
                            return True, None
                    except Exception as e:
                        logging.debug(f"curl check failed: {e}")
            
            # All attempts failed - determine error reason
            error_reason = "Stream does not work"
            
            # Try to extract more specific error message
            if "403 Forbidden" in error_output:
                error_reason = "Access forbidden (403)"
            elif "404 Not Found" in error_output:
                error_reason = "Stream not found (404)"
            elif "401 Unauthorized" in error_output:
                error_reason = "Authentication required (401)"
            elif "Protocol not found" in error_output:
                error_reason = "Protocol not supported"
            elif "Connection refused" in error_output:
                error_reason = "Connection refused"
            elif "Unable to open resource" in error_output:
                error_reason = "Unable to open resource"
            
            # Only mark as failed on last retry attempt
            if attempt == RETRY_COUNT:
                stats.failed += 1
                cache[url] = (False, error_reason)
                return False, error_reason

        except subprocess.TimeoutExpired:
            logging.error(f"ffmpeg timeout for {channel_name} (attempt {attempt + 1})")
            if attempt == RETRY_COUNT:
                stats.timeout += 1
                cache[url] = (False, "ffmpeg timeout")
                return False, "ffmpeg timeout"

        except requests.exceptions.RequestException as e:
            logging.error(f"Request error for {channel_name} (attempt {attempt + 1}): {e}", exc_info=True)
            simplified_error = simplify_error(str(e))
            if attempt == RETRY_COUNT:
                stats.failed += 1
                cache[url] = (False, simplified_error)
                return False, simplified_error

        except Exception as e:
            logging.error(f"General error for {channel_name}: {e}", exc_info=True)
            if attempt == RETRY_COUNT:
                stats.failed += 1
                cache[url] = (False, "General error")
                return False, "General error"
            
        # Small delay between retries
        time.sleep(2)

def simplify_error(error_message: str) -> str:
    error_map = {
        "No connection adapters": "No connection!",
        "Timeout": "Request timeout",
        "403 Forbidden": "Access forbidden (403)"
    }
    for error, message in error_map.items():
        if error in error_message:
            return message
    return "Request error"


def get_unique_filename(directory: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    new_filename = filename
    for i in range(1, 101):
        if not os.path.exists(os.path.join(directory, new_filename)):
            break
        new_filename = f"{base}_{i}{ext}"
    return new_filename


def add_extm3u_line(content: str) -> str:
    return "#EXTM3U\n" + content


def process_playlist(playlist: str, save_file: Optional[str], num_threads: int = NUM_THREADS, ffmpeg_timeout: int = FFMPEG_TIMEOUT):
    check_dependencies()
    if not save_file:
        save_file = os.path.join('output', get_unique_filename('output', 'default.m3u'))

    if playlist.startswith('http'):
        try:
            content = requests.get(playlist).text
        except requests.RequestException as e:
            logging.error(f"Failed to download playlist: {e}")
            sys.exit(1)
    else:
        try:
            with open(playlist, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except FileNotFoundError:
            logging.error(f"File {playlist} not found")
            sys.exit(1)
        except IOError as e:
            logging.error(f"Error reading file {playlist}: {e}")
            sys.exit(1)

    # Make sure content has the #EXTM3U header
    if not content.strip().startswith("#EXTM3U"):
        content = "#EXTM3U\n" + content

    # Parse the playlist content
    channels = parse_playlist(content)
    
    logging.info(f"Found {len(channels)} channels in the playlist")
    print(f"{Fore.CYAN}Found {len(channels)} channels in the playlist{Style.RESET_ALL}")
    
    updated_lines = ["#EXTM3U"]  # Start with the M3U header

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        future_to_channel = {}
        pbar = tqdm(total=len(channels), desc="Checking channels", ncols=100, colour="green")

        # Process each channel
        for channel in channels:
            url = channel['url']
            channel_name = extract_channel_name(channel['extinf'])
            
            # Extract headers from options
            headers = extract_headers_from_options(channel['options'])
                
            future = executor.submit(check_stream, url, channel_name, headers, ffmpeg_timeout)
            future_to_channel[future] = channel

        try:
            for future in concurrent.futures.as_completed(future_to_channel):
                channel = future_to_channel[future]
                channel_name = extract_channel_name(channel['extinf'])
                
                try:
                    success, error = future.result()
                    if success:
                        # Add this channel to the updated playlist
                        updated_lines.append(channel['extinf'])
                        for option in channel['options']:
                            updated_lines.append(option)
                        updated_lines.append(channel['url'])
                        print(f"{Fore.GREEN}[SUCCESS] {channel_name}{Style.RESET_ALL}")
                    else:
                        print(f"{Fore.RED}[FAIL] {channel_name} - {error}{Style.RESET_ALL}")
                        logging.error(f"Failed to play {channel['url']}: {error}")
                except concurrent.futures.TimeoutError:
                    with lock:
                        print(f"{Fore.YELLOW}[SKIPPED] {channel_name} - Took too long{Style.RESET_ALL}")
                        stats.skipped += 1
                        with open(SKIPPED_FILE_PATH, 'a', encoding='utf-8') as f:
                            f.write(f"{channel['extinf']}\n")
                            for option in channel['options']:
                                f.write(f"{option}\n")
                            f.write(f"{channel['url']}\n")
                pbar.update(1)

        except concurrent.futures.TimeoutError:
            print(f"{Fore.RED}Processing took too long!{Style.RESET_ALL}")

        finally:
            pbar.close()

    with open(save_file, 'w', encoding='utf-8') as f:
        for line in updated_lines:
            f.write(line + "\n")

    print(f"\n{Fore.CYAN}Playlist saved to {save_file}{Style.RESET_ALL}")
    stats.log_summary()
    stats.print_summary()


def extract_channel_name(extinf_line: str) -> str:
    """Extract channel name from EXTINF line."""
    if ',' in extinf_line:
        return extinf_line.split(',', 1)[1].strip()
    return "Unknown"


def extract_headers_from_options(options: list) -> dict:
    """Extract all possible headers from channel options."""
    headers = {}
    
    for option in options:
        option_lower = option.lower()
        
        # VLC options (most common)
        if option.startswith('#EXTVLCOPT:http-user-agent='):
            headers['User-Agent'] = option[27:].strip()
        elif option.startswith('#EXTVLCOPT:http-referrer='):
            headers['Referer'] = option[25:].strip()
        elif option.startswith('#EXTVLCOPT:http-origin='):
            headers['Origin'] = option[21:].strip()
        elif option.startswith('#EXTVLCOPT:http-header='):
            header_line = option[22:].strip()
            if ':' in header_line:
                key, value = header_line.split(':', 1)
                headers[key.strip()] = value.strip()
        # Generic VLC options format
        elif option.startswith('#EXTVLCOPT:'):
            option_value = option[11:].strip()
            if option_value.startswith('http-'):
                parts = option_value.split('=', 1)
                if len(parts) == 2:
                    header_name = parts[0].replace('http-', '')
                    # Convert to standard header name
                    if header_name.lower() == 'user-agent':
                        header_name = 'User-Agent'
                    elif header_name.lower() == 'referrer':
                        header_name = 'Referer'
                    elif header_name.lower() == 'origin':
                        header_name = 'Origin'
                    else:
                        # Convert header name to standard format (Title-Case)
                        header_name = '-'.join(word.capitalize() for word in header_name.split('-'))
                    headers[header_name] = parts[1].strip()
                
        # Kodi properties
        elif option.startswith('#KODIPROP:inputstream.adaptive.stream_headers='):
            header_part = option[43:].strip()
            for header_pair in header_part.split('&'):
                if '=' in header_pair:
                    key, value = header_pair.split('=', 1)
                    # Convert common header names to standard format
                    key = key.strip()
                    if key.lower() == 'user-agent':
                        headers['User-Agent'] = value.strip()
                    elif key.lower() == 'referer' or key.lower() == 'referrer':
                        headers['Referer'] = value.strip() 
                    elif key.lower() == 'origin':
                        headers['Origin'] = value.strip()
                    else:
                        # Convert to proper header case
                        key = '-'.join(word.capitalize() for word in key.split('-'))
                        headers[key] = value.strip()
        
        # Generic Kodi properties that might contain headers
        elif option.startswith('#KODIPROP:'):
            if '=' in option:
                prop, value = option[10:].split('=', 1)
                prop = prop.strip().lower()
                value = value.strip()
                
                if prop == 'user-agent':
                    headers['User-Agent'] = value
                elif prop in ('referer', 'referrer'):
                    headers['Referer'] = value
                elif prop == 'origin':
                    headers['Origin'] = value
                elif prop.endswith('.useragent'):
                    headers['User-Agent'] = value
                elif prop == 'http-user-agent':
                    headers['User-Agent'] = value
                elif prop == 'http-referrer' or prop == 'http-referer':
                    headers['Referer'] = value
                elif prop == 'http-origin':
                    headers['Origin'] = value
        
        # Check for headers in the EXTINF line (common in some playlists)
        elif option.startswith('#EXTINF:'):
            # Extract User-Agent
            if 'user-agent=' in option_lower:
                ua_start = option_lower.find('user-agent=')
                if ua_start > 0:
                    ua_part = option[ua_start + 11:]
                    # Extract the user agent value (might be quoted or up to next space or comma)
                    if ua_part.startswith('"'):
                        end_quote = ua_part.find('"', 1)
                        if end_quote > 0:
                            headers['User-Agent'] = ua_part[1:end_quote]
                    elif ' ' in ua_part:
                        headers['User-Agent'] = ua_part.split(' ', 1)[0]
                    elif ',' in ua_part:
                        headers['User-Agent'] = ua_part.split(',', 1)[0]
                    else:
                        headers['User-Agent'] = ua_part
            
            # Extract Referer
            if 'referrer=' in option_lower or 'referer=' in option_lower:
                ref_keyword = 'referrer=' if 'referrer=' in option_lower else 'referer='
                ref_start = option_lower.find(ref_keyword)
                if ref_start > 0:
                    ref_part = option[ref_start + len(ref_keyword):]
                    # Extract the referrer value
                    if ref_part.startswith('"'):
                        end_quote = ref_part.find('"', 1)
                        if end_quote > 0:
                            headers['Referer'] = ref_part[1:end_quote]
                    elif ' ' in ref_part:
                        headers['Referer'] = ref_part.split(' ', 1)[0]
                    elif ',' in ref_part:
                        headers['Referer'] = ref_part.split(',', 1)[0]
                    else:
                        headers['Referer'] = ref_part
            
            # Extract Origin (added)
            if 'origin=' in option_lower:
                origin_start = option_lower.find('origin=')
                if origin_start > 0:
                    origin_part = option[origin_start + 7:]
                    if origin_part.startswith('"'):
                        end_quote = origin_part.find('"', 1)
                        if end_quote > 0:
                            headers['Origin'] = origin_part[1:end_quote]
                    elif ' ' in origin_part:
                        headers['Origin'] = origin_part.split(' ', 1)[0]
                    elif ',' in origin_part:
                        headers['Origin'] = origin_part.split(',', 1)[0]
                    else:
                        headers['Origin'] = origin_part
    
    # Simple logging to see what headers were found
    if headers:
        logging.debug(f"Extracted headers: {headers}")
    
    return headers

def main():
    parser = argparse.ArgumentParser(description="IPTV playlist checker")
    parser.add_argument('-p', '--playlist', help="URL or path to the playlist file")
    parser.add_argument('-s', '--save', help="Path to save the checked playlist")
    parser.add_argument('-t', '--threads', type=int, default=NUM_THREADS, help="Number of threads for checking streams")
    parser.add_argument('-ft', '--ffmpeg-timeout', type=int, default=FFMPEG_TIMEOUT, help="Timeout for ffmpeg (in seconds)")
    parser.add_argument('-file', action="store_true", help="Process all playlist files from the input folder")
    args = parser.parse_args()

    if args.file:
        input_dir = 'input'
        output_dir = 'output'
        os.makedirs(output_dir, exist_ok=True)
        process_files_in_directory(input_dir, output_dir, args.threads, args.ffmpeg_timeout)
    else:
        if not args.playlist:
            parser.error("Playlist URL or file path is required unless using -file option.")
        process_playlist(args.playlist, args.save, args.threads, args.ffmpeg_timeout)


if __name__ == '__main__':
    main()