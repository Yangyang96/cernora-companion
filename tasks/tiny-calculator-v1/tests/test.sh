#!/bin/sh
set -u

mkdir -p /logs/verifier/candidate
cp -a /workspace/. /logs/verifier/candidate/
started_milliseconds=$(date +%s%3N)
PYTHONDONTWRITEBYTECODE=1 python /tests/run_tests.py \
  --candidate-root /logs/verifier/candidate \
  > /logs/verifier/stdout.txt \
  2> /logs/verifier/stderr.txt
status=$?
finished_milliseconds=$(date +%s%3N)
duration_milliseconds=$((finished_milliseconds - started_milliseconds))
printf '%s\n' "$status" > /logs/verifier/exit-code.txt
printf '%s\n' "$duration_milliseconds" > /logs/verifier/duration-milliseconds.txt
if [ "$status" -eq 0 ]; then
  printf '1\n' > /logs/verifier/reward.txt
else
  printf '0\n' > /logs/verifier/reward.txt
fi
exit 0
