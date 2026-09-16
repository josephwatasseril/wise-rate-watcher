import json
import os
import sys
from pathlib import Path
import requests

SOURCE = os.getenv("SOURCE_CURRENCY", "USD").strip().upper()
TARGET = os.getenv("TARGET_CURRENCY", "EUR").strip().upper()
INITIAL_THRESHOLD = float(os.getenv("INITIAL_THRESHOLD", "0.8718"))
NTFY_TOPIC = os.environ["NTFY_TOPIC"].strip()
STATE_FILE = Path("state.json")

def get_wise_rate(source: str, target: str) -> float:
    # Public frontend endpoint that does not require authentication
    url = f"https://wise.com/rates/live?source={source}&target={target}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }

    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    data = response.json()

    # The public endpoint returns {"source": "USD", "target": "EUR", "value": 0.9234, ...}
    if "value" not in data:
        raise ValueError(f"Unexpected response payload from Wise: {data}")

    return float(data["value"])


def load_highest_rate(pair_key: str, initial_baseline: float) -> float:
    recorded_high = 0.0
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                recorded_high = float(data.get(pair_key, 0.0))
        except (json.JSONDecodeError, ValueError):
            recorded_high = 0.0

    # Ensures any higher value set in INITIAL_THRESHOLD is respected
    return max(recorded_high, initial_baseline)


def save_highest_rate(pair_key: str, new_high: float):
    data = {}
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            data = {}

    data[pair_key] = new_high
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def send_ntfy_alert(rate: float, prev_high: float, source: str, target: str, topic: str):
    url = f"https://ntfy.sh/{topic}"
    message = (
        f"New peak for {source}/{target}: {rate:.4f}\n"
        f"Previous peak / threshold: {prev_high:.4f}"
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

    print(f"Checking pair:    {pair_key}")
    print(f"Current Rate:     {current_rate:.4f}")
    print(f"Target Benchmark: {last_highest:.4f}")

    updated = False
    if current_rate > last_highest:
        print(f"New high reached! Sending ntfy alert and updating state...")
        send_ntfy_alert(current_rate, last_highest, SOURCE, TARGET, NTFY_TOPIC)
        save_highest_rate(pair_key, current_rate)
        updated = True
    else:
        print("Rate has not exceeded the last peak. No alert needed.")

    # Expose output flag to the GitHub Actions runner
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh_out:
            gh_out.write(f"updated={'true' if updated else 'false'}\n")


if __name__ == "__main__":
    main()