"""DB management CLI (storage.md §6): init/revision/migrate/status/seed/import, plus
org-token issue/revoke/list (otel-intake design §4.5)."""
import argparse
import json
import sys

from .config import load_config
from .dbm import operations
from .engine import get_engine
from .log import get_logger
from .org_token_store import HASH_PREFIX_LEN, PgOrgTokenStore

log = get_logger()


def _org_token(cfg, args) -> str:
    """The `org-token` verb group. Unlike the schema verbs it goes through the ORM, so it
    needs the process-wide engine initialized first."""
    get_engine(cfg)
    store = PgOrgTokenStore()

    if args.token_verb == "issue":
        descriptor = json.loads(args.descriptor) if args.descriptor else {}
        if not isinstance(descriptor, dict):
            raise ValueError("--descriptor must be a JSON object")
        api_key, token_hash = store.issue(args.org, descriptor)
        # The one and only time the plaintext exists outside the client. Written to stdout
        # and never logged: the structured log below carries the hash prefix, which is what
        # an operator needs later to revoke it (arch §18.1 -- replaced, never recovered).
        sys.stdout.write(f"org-token issued for {args.org}\n")
        sys.stdout.write(f"  key   {api_key}\n")
        sys.stdout.write(f"  hash  {token_hash[:HASH_PREFIX_LEN]}…\n")
        sys.stdout.write("  store this key now -- it cannot be recovered\n")
        log.info("org_token_issued", org_id=args.org,
                 hash_prefix=token_hash[:HASH_PREFIX_LEN])
        return "issued"

    if args.token_verb == "revoke":
        matches = store.find_by_hash_prefix(args.hash_prefix)
        if not matches:
            raise ValueError(f"no org token with hash prefix {args.hash_prefix!r}")
        if len(matches) > 1:
            # Refuse rather than guess: revoking the wrong org's fleet is silent until the
            # exports stop.
            raise ValueError(
                f"hash prefix {args.hash_prefix!r} matches {len(matches)} tokens; "
                "use more characters"
            )
        store.set_revoked(matches[0].token_hash, True)
        log.info("org_token_revoked", org_id=matches[0].org_id,
                 hash_prefix=matches[0].token_hash[:HASH_PREFIX_LEN])
        return "revoked"

    if args.token_verb == "list":
        for row in store.list_for_org(args.org):
            state = "revoked" if row.revoked else "active"
            sys.stdout.write(
                f"{row.token_hash[:HASH_PREFIX_LEN]}  {state:8}  {row.created_at:%Y-%m-%d}  "
                f"{json.dumps(row.descriptor)}\n"
            )
        return "listed"

    raise ValueError(f"unknown org-token verb {args.token_verb!r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cli_db")
    ap.add_argument("--config", default="config/storage.yaml")
    sub = ap.add_subparsers(dest="verb")

    p_init = sub.add_parser("init")
    p_init.add_argument("--force", action="store_true")
    p_rev = sub.add_parser("revision")
    p_rev.add_argument("-m", "--message", required=True)
    p_rev.add_argument("--no-autogenerate", action="store_true")
    p_mig = sub.add_parser("migrate")
    p_mig.add_argument("--sql", action="store_true")
    sub.add_parser("status")
    sub.add_parser("seed")
    p_imp = sub.add_parser("import")
    p_imp.add_argument("--source", required=True)

    p_tok = sub.add_parser("org-token", help="org ingest tokens for the OTLP routes")
    tok = p_tok.add_subparsers(dest="token_verb", required=True)
    p_tok_issue = tok.add_parser("issue")
    p_tok_issue.add_argument("--org", required=True)
    p_tok_issue.add_argument("--descriptor", help="JSON object: who/what this key is for")
    p_tok_revoke = tok.add_parser("revoke")
    p_tok_revoke.add_argument("--hash-prefix", required=True, dest="hash_prefix")
    p_tok_list = tok.add_parser("list")
    p_tok_list.add_argument("--org", required=True)

    # argparse rejects an unrecognized verb by printing an error and calling
    # sys.exit(2); catch that here so `main` always returns an int instead of
    # raising SystemExit for invalid CLI input (storage.md §6).
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code in (0, None) else 1

    if args.verb is None:
        ap.print_help()
        return 1

    try:
        cfg = load_config(args.config)
        if args.verb == "init":
            result = operations.init(cfg, force=args.force)
            log.info("db_init", mode=result, force=args.force)
        elif args.verb == "revision":
            result = operations.revision(cfg, args.message, autogenerate=not args.no_autogenerate)
            log.info("db_revision", auto=not args.no_autogenerate)
        elif args.verb == "migrate":
            result = operations.migrate(cfg, sql=args.sql)
            log.info("db_migrate", sql=args.sql)
        elif args.verb == "status":
            st = operations.status(cfg)
            log.info("db_status", **st)
            sys.stdout.write(f"{st}\n")
            result = "ok"
        elif args.verb == "seed":
            result = operations.seed(cfg)
            log.info("db_seed", result=result)
        elif args.verb == "import":
            operations.import_dump(cfg, args.source)
            result = "imported"
        elif args.verb == "org-token":
            result = _org_token(cfg, args)
        else:
            return 1
    except NotImplementedError as e:
        sys.stderr.write(f"{e}\n")
        return 1
    except Exception as e:  # surface a clean failure; storage errors propagate as text
        sys.stderr.write(f"error: {e}\n")
        return 1
    sys.stdout.write(f"{args.verb}: {result}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
