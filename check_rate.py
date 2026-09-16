import json
import os
import sys
from pathlib import Path
import requests

SOURCE = os.getenv("SOURCE_CURRENCY").strip().upper()
TARGET = os.getenv("TARGET_CURRENCY").strip().upper()
INITIAL_THRESHOLD = float(os.getenv("INITIAL_THRESHOLD"))
NTFY_TOPIC = os.environ["NTFY_TOPIC"].strip()

CACHE_DIR = Path(".cache")
STATE_FILE = CACHE_DIR / "state.json"


def get_wise_rate(source: str, target: str) -> float:
    url = f"https://wise.com/rates/live?source={source}&target={target}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    data = response.json()

    if "value" not in data:
        raise ValueError(f"Unexpected response payload: {data}")

    return float(data["value"])


def load_highest_rate(pair_key: str, baseline: float) -> float:
    recorded_high = 0.0
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                recorded_high = float(data.get(pair_key, 0.0))
        except (json.JSONDecodeError, ValueError):
            recorded_high = 0.0

    return max(recorded_high, baseline)


def save_highest_rate(pair_key: str, rate: float):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {}
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError):
            data = {}

    data[pair_key] = rate
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def send_ntfy_alert(rate: float, prev_high: float, source: str, target: str, topic: str):
    url = f"https://ntfy.sh/{topic}"
    message = (
        f"Wise rate for {source}/{target} reached a new peak: {rate:.4f}\n"
        f"Previous benchmark: {prev_high:.4f}"
    )
    headers = {
        "Title": f"Wise Rate Peak: {source}/{target} hit {rate:.4f}",
        "Priority": "high",
        "Tags": "chart_with_upwards_trend,moneybag",
    }
    response = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
    response.raise_for_status()


def main():
    pair_key = f"{SOURCE}_{TARGET}"
    current_rate = get_wise_rate(SOURCE, TARGET)
    last_highest = load_highest_rate(pair_key, INITIAL_THRESHOLD)

    print(f"Currency Pair:    {pair_key}")
    print(f"Current Rate:     {current_rate:.4f}")
    print(f"Benchmark/Peak:   {last_highest:.4f}")

    if current_rate > last_highest:
        print("New peak reached! Sending push notification...")
        send_ntfy_alert(current_rate, last_highest, SOURCE, TARGET, NTFY_TOPIC)
        save_highest_rate(pair_key, current_rate)
    else:
        print("Rate has not exceeded the benchmark. No alert dispatched.")
        # Ensure state file exists on disk so actions/cache always packages the folder
        if not STATE_FILE.exists():
            save_highest_rate(pair_key, last_highest)


if __name__ == "__main__":
    main()