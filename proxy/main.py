"""Lightweight reverse proxy for Polymarket CLOB API.
Deployed on Fly.io (Europe) to bypass US geo-restrictions.
"""

import os
import requests as req
from flask import Flask, request, Response

app = Flask(__name__)
TARGET = "https://clob.polymarket.com"

# Headers that should not be forwarded between client <-> proxy
HOP_BY_HOP = frozenset([
    'connection', 'keep-alive', 'proxy-authenticate',
    'proxy-authorization', 'te', 'trailers',
    'transfer-encoding', 'upgrade', 'host',
    'content-length', 'content-encoding',
])


@app.route('/health')
def health():
    return 'ok'


@app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])
def proxy(path):
    url = f"{TARGET}/{path}"

    # Forward all headers except hop-by-hop
    headers = {k: v for k, v in request.headers if k.lower() not in HOP_BY_HOP}

    try:
        resp = req.request(
            method=request.method,
            url=url,
            headers=headers,
            data=request.get_data(),
            params=request.args,
            timeout=30,
            allow_redirects=False,
        )
    except req.RequestException as e:
        return Response(f'Proxy error: {e}', status=502)

    # Strip hop-by-hop from response too
    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in HOP_BY_HOP}

    return Response(resp.content, status=resp.status_code, headers=resp_headers)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
