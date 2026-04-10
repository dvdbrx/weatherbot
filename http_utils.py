"""
http_utils.py — HTTP request helper with retry logic.
Wraps requests.get/post with exponential backoff.
"""

import time
import requests
from typing import Any


def get_json(
    url: str,
    *,
    timeout: tuple[int, int] = (5, 8),
    retries: int = 3,
    backoff: float = 1.0,
    params: dict | None = None,
) -> dict[str, Any] | list | None:
    """GET a URL and return parsed JSON, with retry on transient failures.

    Returns None if all retries are exhausted.
    """
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=timeout, params=params)
            r.raise_for_status()
            return r.json()
        except (requests.ConnectionError, requests.Timeout) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
        except requests.HTTPError as e:
            # Don't retry on 4xx client errors
            if e.response is not None and 400 <= e.response.status_code < 500:
                return None
            last_err = e
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
        except Exception:
            return None
    return None


def post_json(
    url: str,
    json_data: dict,
    *,
    timeout: int = 5,
    retries: int = 3,
    backoff: float = 1.0,
) -> dict[str, Any] | None:
    """POST JSON and return parsed response, with retry on transient failures."""
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(url, json=json_data, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except (requests.ConnectionError, requests.Timeout) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
        except Exception:
            return None
    return None
