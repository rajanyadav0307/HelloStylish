import requests


def get_json(url: str, timeout: int = 10) -> dict:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()
