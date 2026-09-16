import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

SOURCE = os.getenv("SOURCE_CURRENCY").strip().upper()
TARGET = os.getenv("TARGET_CURRENCY").strip().upper()
NTFY_TOPIC = os.getenv("NTFY_TOPIC").strip()

raw_initial = os.getenv("INITIAL_THRESHOLD").strip()
try:
    INITIAL_THRESHOLD = Decimal(raw_initial)
except InvalidOperation:
    INITIAL_THRESHOLD = Decimal("0.0")

CACHE_DIR = Path(".cache")
STATE_FILE = CACHE_DIR / "state.json"


def get_http_session() -> requests.Session:
    """Builds a requests Session configured with automatic retries and exponential backoff."""
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_wise_rate(session: requests.Session, source: str, target: str) -> Decimal:
    """Fetches the live exchange rate directly into a Decimal to preserve exact precision."""
    url = f"https://wise.com/rates/live?source={source}&target={target}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    response = session.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    data = response.json()

    if "value" not in data:
        raise ValueError(f"Unexpected response payload from Wise: {data}")

    # Convert through string to prevent float representation artifacts
    return Decimal(str(data["value"]))


def load_highest_rate(pair_key: str, baseline: Decimal) -> Decimal:
    recorded_high = Decimal("0.0")
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                val = data.get(pair_key)
                if val is not None:
                    recorded_high = Decimal(str(val))
        except (json.JSONDecodeError, InvalidOperation, ValueError):
            recorded_high = Decimal("0.0")

    return max(recorded_high, baseline)


def save_highest_rate(pair_key: str, rate: Decimal):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {}
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError):
            data = {}

    data[pair_key] = str(rate)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def send_ntfy_alert(session: requests.Session, rate: Decimal, prev_high: Decimal, source: str, target: str, topic: str):
    url = f"https://ntfy.sh/{topic}"
    message = (
        f"Wise rate for {source}/{target} reached a new peak: {rate}\n"
        f"Previous benchmark: {prev_high}"
    )
    headers = {
        "Title": f"Wise Rate Peak: {source}/{target} hit {rate}",
        "Priority": "high",
        "Tags": "chart_with_upwards_trend,moneybag",
    }
    response = session.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
    response.raise_for_status()


def write_github_summary(source: str, target: str, current: Decimal, benchmark: Decimal, alerted: bool):
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    result_text = "🚀 **Alert Dispatched (New Peak)**" if alerted else "⏸️ No Alert (Below Peak)"
    markdown = f"""### 💱 Wise Rate Checker Summary

| Metric | Value |
| :--- | :--- |
| **Currency Pair** | `{source}/{target}` |
| **Current Rate** | **`{current}`** |
| **Benchmark / Last Peak** | `{benchmark}` |
| **Status** | {result_text} |
"""
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write(markdown)


def main():
    if not NTFY_TOPIC:
        print("Error: NTFY_TOPIC secret is missing or empty.", file=sys.stderr)
        sys.exit(1)

    session = get_http_session()
    pair_key = f"{SOURCE}_{TARGET}"

    current_rate = get_wise_rate(session, SOURCE, TARGET)
    last_highest = load_highest_rate(pair_key, INITIAL_THRESHOLD)

    print(f"Currency Pair:    {pair_key}")
    print(f"Current Rate:     {current_rate}")
    print(f"Benchmark/Peak:   {last_highest}")

    alerted = False
    if current_rate > last_highest:
        print("New peak reached! Sending push notification...")
        send_ntfy_alert(session, current_rate, last_highest, SOURCE, TARGET, NTFY_TOPIC)
        save_highest_rate(pair_key, current_rate)
        alerted = True
    else:
        print("Rate has not exceeded the benchmark. No alert dispatched.")
        if not STATE_FILE.exists():
            save_highest_rate(pair_key, last_highest)

    write_github_summary(SOURCE, TARGET, current_rate, last_highest, alerted)


if __name__ == "__main__":
    main()