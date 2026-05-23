import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_USER_INFO_URL = "https://api.web.finmindtrade.com/v2/user_info"
REQUEST_TIMEOUT = 20
SSL_CONTEXT = ssl._create_unverified_context()
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) StockScanner/2.0"


class FinMindAbort(Exception):
    pass


def load_dotenv_file(path):
    if not path or not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class FinMindClient:
    def __init__(self, app_dir=None, logger=None, defaults=None):
        if app_dir:
            load_dotenv_file(os.path.join(app_dir, ".env"))
        defaults = defaults or {}
        self.logger = logger or (lambda message: None)
        self.enabled = env_flag("ENABLE_FINMIND", False)
        self.token = (os.environ.get("FINMIND_API_TOKEN") or "").strip()
        self.min_remaining = env_int("FINMIND_MIN_REMAINING", defaults.get("min_remaining", 80))
        self.sleep_seconds = env_float("FINMIND_SLEEP_SECONDS", defaults.get("sleep_seconds", 0.7))
        self._request_counter = 0

    def log(self, message):
        if self.logger:
            self.logger(message)

    def is_enabled(self):
        return self.enabled

    def _headers(self):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request_json(self, url, params=None):
        if self._request_counter and self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)
        self._request_counter += 1
        query_url = url
        if params:
            query_url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(query_url, headers=self._headers())
        try:
            with urllib.request.urlopen(
                request,
                timeout=REQUEST_TIMEOUT,
                context=SSL_CONTEXT,
            ) as response:
                payload = json.loads(response.read().decode("utf-8-sig"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8-sig", errors="ignore")
            except Exception:
                body = ""
            if exc.code in (401, 402, 403, 429):
                raise FinMindAbort(f"FinMind HTTP {exc.code}: {body or exc.reason}")
            raise
        except Exception as exc:
            raise FinMindAbort(f"FinMind request failed: {exc}") from exc

        message = str(payload.get("msg", "")).strip()
        if payload.get("status") not in (None, 200):
            if "Token is illegal" in message or "Your level is free" in message:
                raise FinMindAbort(message)
            raise FinMindAbort(message or "FinMind API error")
        return payload

    def check_quota(self):
        if not self.enabled or not self.token:
            return None
        payload = self._request_json(FINMIND_USER_INFO_URL)
        user_count = int(payload.get("user_count") or 0)
        api_request_limit = int(payload.get("api_request_limit") or 0)
        return api_request_limit - user_count

    def fetch_dataset(self, dataset, start_date, end_date, data_id=""):
        if not self.enabled:
            return []
        params = {
            "dataset": dataset,
            "start_date": str(start_date),
            "end_date": str(end_date),
        }
        if data_id:
            params["data_id"] = data_id
        payload = self._request_json(FINMIND_DATA_URL, params)
        data = payload.get("data") or []
        if not isinstance(data, list):
            return []
        return data
