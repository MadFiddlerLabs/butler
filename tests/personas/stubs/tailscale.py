"""The `tailscale` command on a persona-test computer, once the persona has "installed"
Tailscale: the state is in ~/persona-fakes/tailscale.json (signed in, the machine's name, what
`serve` shares). It answers the commands Butler and its skills use."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import events  # noqa: E402

STATE = events.FAKES / "tailscale.json"


def main(args: list[str]) -> int:
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if not state.get("up"):
        print("Logged out.\nLog in at: https://login.tailscale.com/a/1a2b3c4d", file=sys.stderr)
        return 1
    host, tailnet, login = state["hostname"], state["tailnet"], state["login"]
    command = args[0] if args else "status"
    if command == "status":
        if "--json" in args:
            print(json.dumps({"BackendState": "Running", "Self": {"HostName": host, "DNSName": f"{host}.{tailnet}.",
                                                                  "TailscaleIPs": ["100.101.102.103"]},
                              "CurrentTailnet": {"Name": login, "MagicDNSSuffix": tailnet}}))
        else:
            print(f"100.101.102.103  {host}  {login}  macOS  -")
        return 0
    if command == "ip":
        print("100.101.102.103")
        return 0
    if command == "set":
        for a in args[1:]:
            if a.startswith("--hostname="):
                state["hostname"] = a.split("=", 1)[1]
        STATE.write_text(json.dumps(state))
        return 0
    if command == "whois":
        print(json.dumps({"UserProfile": {"LoginName": state.get("phone_login", login), "DisplayName": login}})
              if "--json" in args else f"User:\n  Name: {login}")
        return 0
    if command == "serve":
        rest = [a for a in args[1:] if not a.startswith("--")]
        if rest[:1] == ["status"] or not rest:
            print(f"https://{host}.{tailnet} (tailnet only)\n|-- / proxy http://127.0.0.1:{state['serve']}"
                  if state.get("serve") else "No serve config")
            return 0
        if rest[:1] in (["reset"], ["off"]):
            state.pop("serve", None)
        elif not state.get("https"):
            print("Serve requires HTTPS certificates. Enable them in the admin console: "
                  "https://login.tailscale.com/admin/dns", file=sys.stderr)
            return 1
        else:
            state["serve"] = rest[-1].rsplit(":", 1)[-1]
            print(f"Available within your tailnet:\n\nhttps://{host}.{tailnet}/\n|-- proxy http://127.0.0.1:{state['serve']}\n\n"
                  "Serve started and running in the background.")
        STATE.write_text(json.dumps(state))
        return 0
    if command == "cert":
        return 0
    print(f"tailscale {command}: done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
