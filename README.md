# Shopping Sessions — C++ v1

Read [README_zh.md](README_zh.md) for the full Chinese explanation of the rules,
algorithm choices, learning notes, run commands, and verification limits.

The C++ core implements strict CSV parsing, linked identities, global earliest
event deduplication, sessions, last-click attribution, and interval-union assists.
Python / PyArrow serializes final aggregate rows to Parquet.

Verified here: all 36 official fixtures at the computation layer, 30 independently
checked random datasets, and empty datasets at several thread counts.
Not yet verified here: Docker, actual Parquet output, official 1M dev data, or
practice/final-scale performance. See the Chinese README before submitting.

```sh
make binary-fixtures
python3 scripts/random_check.py 30
# With Docker and the checker dependencies installed:
make container-fixtures
make dev run-dev
# Unzip official dev-answer.zip first:
make check-dev
```
