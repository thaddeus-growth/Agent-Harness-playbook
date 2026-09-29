"""The test convention every harness (and the kit itself) keeps.

  * check.py      check(), finish(), tmp_dir(), capture(), one_doc(),
                  clean_env(), raises(), run_functions()
  * run_tests.py  the gate: each file in its own process with a private
                  TMPDIR; a file passes only if it exits 0, prints
                  `RESULT: N passed` with N > 0 and no `, M failed` > 0,
                  and leaves its TMPDIR empty
  * sandbox.py    the env a harness CLI runs under in a test
  * suites.py     the nine day-one tests of a new harness, as library
                  calls (each generated test is one call)

Test: kit/tests/test_run_tests.py, kit/tests/test_check.py,
kit/tests/test_suites.py.
"""
