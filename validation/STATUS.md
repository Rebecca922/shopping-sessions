# Validation status

- GCC 14.2, C++20, optimized build: compiled successfully.
- Official 36 fixtures: all computation-layer rows match exactly (fixtures.log).
- Independent Python BFS / brute-force oracle: 30 randomized dirty datasets match both tables exactly (random.log).
- Empty input at 1, 3, and 8 threads: both result tables empty as expected.
- AddressSanitizer and UndefinedBehaviorSanitizer: all 36 fixtures pass (sanitizer-fixtures.log). Leak detection was disabled because this execution environment uses ptrace, which LeakSanitizer does not support; leak checking is not claimed.
- Python syntax and shell syntax checks: passed.

Not run: PyArrow serialization and official Parquet checker, Docker build/run,
official seed-1 1M dev comparison, larger-scale speed/memory measurements, or
any scoring submission. Docker/Rust/PyArrow were unavailable in this environment;
dependency downloads received network denials. No practice/final runtime is claimed.

The binary-result tests validate the computation before Parquet encoding. They do
not establish that the container or output serialization passes the scorer.
