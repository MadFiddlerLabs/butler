"""Loaded by every Python a SetupHome starts on Windows (its PYTHONPATH names this folder). The
password store and the PATH change become files in the test's folder, so a test never touches
the runner's own Credential Manager or user PATH. The store is the JSON file that fakebin's
`security` keeps on a Mac (BUTLER_FAKE_KEYCHAIN), so SetupHome.secrets() reads both."""
import json
import os

if os.environ.get("BUTLER_FAKE_KEYCHAIN"):
    from butler import system

    def _load() -> dict:
        try:
            with open(os.environ["BUTLER_FAKE_KEYCHAIN"], encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            return {}

    def get_secret(service: str) -> str | None:
        return _load().get(service) or None

    def has_secret(service: str) -> bool:
        return service in _load()

    def store_secret(service: str, secret: str) -> None:
        store = _load()
        store[service] = secret
        with open(os.environ["BUTLER_FAKE_KEYCHAIN"], "w", encoding="utf-8") as f:
            json.dump(store, f)

    def add_to_path(bin_dir: str) -> bool:
        path = os.environ["BUTLER_FAKE_PATH"]
        added = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                added = f.read().splitlines()
        if bin_dir in added:
            return False
        with open(path, "a", encoding="utf-8") as f:
            f.write(bin_dir + "\n")
        return True

    system.get_secret, system.has_secret, system.store_secret = get_secret, has_secret, store_secret
    system.add_to_path = add_to_path
