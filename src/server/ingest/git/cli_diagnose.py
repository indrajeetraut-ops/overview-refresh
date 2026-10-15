"""Webhook + API diagnostic. Run: python -m server.ingest.git.cli_diagnose [--config PATH]

Answers the two POC failure questions from GitHub's side, using only the App JWT:

  "Why isn't the webhook receiving anything?"
      GET /app/hook/deliveries shows what GitHub TRIED to deliver and the HTTP status it got.
        - nothing recent            -> GitHub never sent: check the App is installed, events are
                                       subscribed, the webhook URL is set + Active, tunnel is up.
        - recent, status_code=401   -> GitHub sent, we rejected: webhook secret mismatch. The
                                       delivery never lands in git_deliveries, which is why
                                       querying it looks like silence.
        - recent, status_code=202   -> it IS working; check the `git_deliveries` table.
        - status_code=0 / 5xx / t.o -> GitHub couldn't reach the tunnel: receiver down or URL wrong.

  "Why do the API probes 404?"
      Lists each installation's account TYPE and the repos the token can actually see. A repo not
      in this list is not in the installation's scope -> every REST/GraphQL call for it 404s. On a
      personal-account 'only select repositories' install, this is the usual cause.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .client import GitHubApiError, GitHubClient
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose the GitHub webhook + API setup.")
    parser.add_argument("--config", default="config/github.yaml")
    parser.add_argument("--deliveries", type=int, default=15, help="how many recent deliveries")
    args = parser.parse_args()

    config = load_config(args.config)

    print("=" * 72)
    print("CONFIG  (secrets shown only as present/absent -- never printed)")
    print("=" * 72)
    checks = {
        "client_id / app_id": bool(config.client_id or config.app_id),
        "private_key_path set": bool(config.private_key_path),
        "private key readable": bool(config.private_key_path) and Path(config.private_key_path).is_file(),
        "webhook_secret set": bool(config.webhook_secret),
    }
    for label, ok in checks.items():
        print(f"  [{'OK ' if ok else 'MISSING'}] {label}")
    if not (checks["client_id / app_id"] and checks["private key readable"]):
        print("\n  Cannot authenticate as the App. Set TALIUM_GITHUB_CLIENT_ID and")
        print("  TALIUM_GITHUB_PRIVATE_KEY_PATH (to the .pem GitHub gave you) and re-run.")
        return

    client = GitHubClient(config)

    print("\n" + "=" * 72)
    print("INSTALLATIONS & REPO SCOPE  (a 404 on a repo means it is NOT listed here)")
    print("=" * 72)
    try:
        installations = client.list_installations()
    except GitHubApiError as e:
        print(f"  FAILED to list installations: {e}")
        print("  -> the App JWT is wrong: check TALIUM_GITHUB_CLIENT_ID / private key path.")
        return
    if not installations:
        print("  none -- the App is not installed anywhere. Install it on your account/org first.")
    for inst in installations:
        acct = inst.get("account", {})
        print(f"\n  installation {inst['id']}  account={acct.get('login')}  "
              f"type={acct.get('type')}  selection={inst.get('repository_selection')}")
        try:
            resp = client.rest_get(inst["id"], "/installation/repositories",
                                   params={"per_page": 100})
            repos = resp.json().get("repositories", [])
            if not repos:
                print("      (token sees zero repos -- nothing was selected at install time)")
            for repo in repos:
                print(f"      {repo['full_name']}   (id={repo['id']})")
        except GitHubApiError as e:
            print(f"      FAILED to list repos: {e}")

    print("\n" + "=" * 72)
    print(f"RECENT WEBHOOK DELIVERIES  (GitHub's own log, last {args.deliveries})")
    print("=" * 72)
    try:
        deliveries = client.list_hook_deliveries(per_page=args.deliveries)
    except GitHubApiError as e:
        print(f"  could not read deliveries: {e}")
        print("  (this endpoint needs the App JWT; if it 404s, the App has no webhook configured)")
        return
    if not deliveries:
        print("  none -- GitHub has not attempted any delivery.")
        print("  -> App not installed, no subscribed event has fired, or the webhook is inactive.")
        return
    print(f"  {'delivered_at':<26} {'event':<22} {'action':<14} status")
    print("  " + "-" * 68)
    for d in deliveries:
        print(f"  {d.get('delivered_at',''):<26} {d.get('event',''):<22} "
              f"{str(d.get('action') or ''):<14} {d.get('status_code')} {d.get('status','')}")
    codes = {d.get("status_code") for d in deliveries}
    print("\n  Reading it:")
    if codes == {202} or codes == {204} or codes <= {202, 204}:
        print("  -> all delivered OK. Successful ones landed in the `git_deliveries` table")
        print("     (psql \"$TALIUM_TEST_DSN\" -c \"select * from git_deliveries order by received_at desc limit 5;\").")
    if 401 in codes:
        print("  -> 401s: GitHub reached the receiver but the signature failed. The webhook")
        print("     secret in the App settings != TALIUM_GITHUB_WEBHOOK_SECRET. Fix and redeliver.")
    if 0 in codes or any((c or 0) >= 500 for c in codes):
        print("  -> 0/5xx: GitHub could not reach the receiver. Tunnel down, wrong URL, or the")
        print("     ingest host was not running. Restart utils/ingest.sh and the tunnel.")


if __name__ == "__main__":
    main()
