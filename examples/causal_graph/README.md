# Causal Graph Examples and Corpus

`graphs/` is the committed scrubbed graph corpus. `query_api_sample.json` is the small fixture
used for API and CLI examples; the other project/session directories exercise real family
layouts and subagents.

Run the storage-neutral builder example:

```bash
PYTHONPATH=src venv/bin/python examples/causal_graph/build_graph.py \
  tests/fixtures/causal_graph/tiny_session.json /tmp/talium-graph.json
```

Compare the two consumer sources:

```bash
PYTHONPATH=src venv/bin/python examples/causal_graph/in_memory_consumer.py

PYTHONPATH=src venv/bin/python examples/causal_graph/file_consumer.py \
  examples/causal_graph/graphs/-Users-sumandra-projects-talium-code-econ/\
0f5e45b9-ecc1-4773-913c-86cd341369d1.json
```

Only graph retrieval differs. Both call `consumer_flow.run_consumer()`, which loads self,
discovers subagents, loads the tree, runs named APIs, `select()`, and QuerySpec, reads reports
and metrics, and unloads the same way.

Run focused named and QuerySpec examples:

```bash
PYTHONPATH=src venv/bin/python examples/causal_graph/run_named_api_examples.py
PYTHONPATH=src venv/bin/python examples/causal_graph/run_queryspec_examples.py
```

The JSON requests in `query_specs/` are standalone QuerySpec inputs. They are not translations
of any retired API. Do not commit source transcripts or credentials; graph fixtures must be
scrubbed before entering this corpus.
