import os
import urllib.parse
from flask import Flask, request, Response, stream_with_context
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

# Create a persistent session with browser impersonation
session = requests.Session()
session.impersonate = "chrome124"

@app.route('/proxy')
def proxy():
    # ROBUST URL EXTRACTION: Prevents '&' characters from truncating the URL
    raw_query = request.query_string.decode('utf-8')
    
    if 'url=' in raw_query:
        target_url = urllib.parse.unquote(raw_query.split('url=', 1)[1])
    else:
        target_url = request.args.get('url')

    if not target_url:
        return "Missing 'url' parameter", 400

    # Thread-safe copy of headers
    req_headers = EXACT_HEADERS.copy()
    
    # Set Referer and Origin to match the curl command EXACTLY
    req_headers["referer"] = "https://frame.y2meta-uk.com/"
    req_headers["origin"] = "https://frame.y2meta-uk.com"

    print(f"[→] Proxying: {target_url[:120]}...")

    try:
        # allow_redirects=True mimics `curl -L`
        resp = session.get(
            target_url, 
            stream=True, 
            headers=req_headers, 
            allow_redirects=True
        )
        print(f"[←] Status: {resp.status_code}")

        if resp.status_code == 403:
            return "403 Forbidden – The tunnel URL may have expired.", 403

        # Exclude headers that break Flask streaming or are handled automatically
        excluded_headers = {
            'content-encoding', 'transfer-encoding', 'content-length', 
            'cf-ray', 'cf-cache-status', 'connection', 'keep-alive', 
            'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'upgrade'
        }
        
        resp_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in excluded_headers
        }
        
        # Ensure Content-Disposition is set to trigger a download
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
