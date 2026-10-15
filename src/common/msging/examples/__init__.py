"""Runnable examples for `common.msging` (design §6.1, §6.2, §6.3).

    srv_hosted.py    the host owns the FastAPI app and includes the router
    srv_make_app.py  make_app(), for a dedicated process
    srv_two.py       two named services in one process
    clnt_full.py     the whole client lifecycle, failures included

Each file is a script: it says at the top exactly how to run it and what to
expect. They share `msging.example.yaml` in this directory -- nothing needs
editing before anything runs.

Importing this package imports nothing else; each example does its own
imports, and only `clnt_full.py` can be imported without pulling FastAPI.
"""
