import os
import random
import urllib.parse
from flask import Flask, request, Response, stream_with_context, jsonify
from curl_cffi import requests

app = Flask(__name__)

# EXACT headers from the successful curl command
EXACT_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en-US,en;q=0.9",
    "priority": "u=0, i",
    "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "cross-site",
    "sec-fetch-storage-access": "active",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
}

# Load proxies from Environment Variable (comma or newline separated)
# Fallback to hardcoded list for local testing if env var is not set
raw_proxy_list = os.environ.get("PROXY_LIST", "")
if raw_proxy_list:
    PROXY_LIST = [p.strip() for p in raw_proxy_list.replace('\n', ',').split(',') if p.strip()]
else:
    PROXY_LIST = [
        "31.59.20.176:6754:lrxwtgbq:k05l1d60pv1l",
        "31.56.127.193:7684:lrxwtgbq:k05l1d60pv1l",
        "45.38.107.97:6014:lrxwtgbq:k05l1d60pv1l",
        "198.105.121.200:6462:lrxwtgbq:k05l1d60pv1l",
        "64.137.96.74:6641:lrxwtgbq:k05l1d60pv1l",
        "198.23.243.226:6361:lrxwtgbq:k05l1d60pv1l",
        "38.154.185.97:6370:lrxwtgbq:k05l1d60pv1l",
        "84.247.60.125:6095:lrxwtgbq:k05l1d60pv1l",
        "142.111.67.146:5611:lrxwtgbq:k05l1d60pv1l",
        "191.96.254.138:6185:lrxwtgbq:k05l1d60pv1l"
    ]

def format_proxy(proxy_str):
    """Converts 'ip:port:user:pass' to curl_cffi proxy dict."""
    if not proxy_str:
        return None
    try:
        ip, port, user, pwd = proxy_str.split(':')
        proxy_url = f"http://{user}:{pwd}@{ip}:{port}"
        return {"http": proxy_url, "https": proxy_url}
    except Exception:
        return None

def extract_video_id(url):
    """Extracts the YouTube video ID from a URL."""
    parsed = urllib.parse.urlparse(url)
    if 'youtube.com' in parsed.netloc:
        return urllib.parse.parse_qs(parsed.query).get('v', [None])[0]
    elif 'youtu.be' in parsed.netloc:
        return parsed.path.strip('/')
    return None

# ==========================================
# SINGLE ENDPOINT: Generate & Prepare Download
# ==========================================
@app.route('/download', methods=['GET'])
def download():
    # 1. API Key Validation (Required)
    provided_key = request.args.get('key')
    expected_key = os.environ.get('API_KEY')
    
    if not expected_key:
        return jsonify({"error": "Server configuration error: API_KEY not set in environment variables."}), 500
        
    if not provided_key or provided_key != expected_key:
        return jsonify({"error": "Unauthorized. Valid API key is required."}), 401

    # 2. Get parameters
    yt_url = request.args.get('url')
    if not yt_url:
        return jsonify({"error": "Missing 'url' parameter. Provide a YouTube link."}), 400

    video_id = extract_video_id(yt_url)
    if not video_id:
        return jsonify({"error": "Invalid YouTube URL provided."}), 400

    fmt = request.args.get('format', 'mp4')
    quality = request.args.get('quality', '1080')
    vcodec = request.args.get('vcodec', 'h264')
    abitrate = request.args.get('abitrate', '320')

    # 3. ONE-TIME USE / FRESH PROCESS: 
    # Shuffle proxies on EVERY request to ensure a fresh, rotated attempt each time.
    available_proxies = PROXY_LIST.copy()
    random.shuffle(available_proxies)

    for proxy_str in available_proxies:
        proxies = format_proxy(proxy_str)
        try:
            # STEP 1: Get Sanity Key (Using the selected proxy)
            key_resp = requests.get(
                f"https://cnv.cx/v2/sanity/key?id={video_id}",
                headers={
                    "Origin": "https://mp3yt.is",
                    "User-Agent": EXACT_HEADERS["user-agent"]
                },
                proxies=proxies,
                timeout=10
            )
            key_data = key_resp.json()
            
            if "key" not in key_data:
                continue  # Proxy failed, try next

            key = key_data["key"]

            # STEP 2: Request Converter Tunnel (Using the EXACT SAME proxy)
            payload = {
                "link": yt_url,
                "format": fmt,
                "audioBitrate": abitrate,
                "videoQuality": quality,
                "vCodec": vcodec
            }
            
            conv_resp = requests.post(
                "https://cnv.cx/v2/converter",
                headers={
                    "Origin": "https://mp3yt.is",
                    "User-Agent": EXACT_HEADERS["user-agent"],
                    "Content-Type": "application/x-www-form-urlencoded",
                    "key": key
                },
                data=payload,
                proxies=proxies,
                timeout=15
            )
            conv_data = conv_resp.json()

            # If successful, construct the download URL and return
            if conv_data.get("status") == "tunnel":
                tunnel_url = conv_data.get("url")
                
                # URL-encode the tunnel URL and proxy string to safely pass them to the /proxy endpoint
                # This ensures the /proxy endpoint uses the SAME proxy that generated the link!
                encoded_tunnel = urllib.parse.quote(tunnel_url, safe='')
                encoded_proxy = urllib.parse.quote(proxy_str, safe='')
                
                proxy_download_url = f"{request.host_url.rstrip('/')}/proxy?url={encoded_tunnel}&proxy={encoded_proxy}"
                
                conv_data["url"] = proxy_download_url
                conv_data["proxy_used"] = proxy_str
                # ---- ONLY CHANGE: add the original tunnel URL as a separate field ----
                conv_data["original_link"] = tunnel_url
                # --------------------------------------------------------------------
                return jsonify(conv_data)
            else:
                continue  # Try next proxy if status isn't "tunnel"

        except Exception as e:
            continue  # Proxy timed out or failed, silently move to the next one

    # If all proxies fail
    return jsonify({"error": "All proxies failed to generate the link. The video may be unavailable or blocked."}), 500


# ==========================================
# STREAMING ENDPOINT: Executes the Download
# ==========================================
@app.route('/proxy')
def proxy():
    # Because the /download endpoint URL-encodes the 'url' parameter, 
    # request.args.get safely decodes it without breaking on '&' characters.
    target_url = request.args.get('url')
    proxy_str = request.args.get('proxy')
    
    if not target_url:
        return "Missing 'url' parameter", 400

    req_headers = EXACT_HEADERS.copy()
    req_headers["referer"] = "https://frame.y2meta-uk.com/"
    req_headers["origin"] = "https://frame.y2meta-uk.com"

    proxies = format_proxy(proxy_str)
    proxy_display = proxy_str or "DIRECT (No Proxy)"

    print(f"[→] Proxying via {proxy_display}: {target_url[:120]}...")

    try:
        resp = requests.get(
            target_url, 
            stream=True, 
            headers=req_headers, 
            proxies=proxies,
            allow_redirects=True
        )
        print(f"[←] Status: {resp.status_code}")

        if resp.status_code == 403:
            return "403 Forbidden – The tunnel URL may have expired or the proxy IP was rejected.", 403

        excluded_headers = {
            'content-encoding', 'transfer-encoding', 'content-length', 
            'cf-ray', 'cf-cache-status', 'connection', 'keep-alive', 
            'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'upgrade'
        }
        
        resp_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in excluded_headers
        }
        
        if 'content-disposition' not in resp_headers:
            resp_headers['Content-Disposition'] = 'attachment; filename="video.mp4"'

        return Response(
            stream_with_context(resp.iter_content(chunk_size=8192)),
            status=resp.status_code,
            headers=resp_headers
        )
    except Exception as e:
        print(f"[✗] Error: {e}")
        return f"Error: {str(e)}", 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8079))
    app.run(host='0.0.0.0', port=port, debug=False)
