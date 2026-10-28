#!/bin/sh
# Throwaway proof helper: remove containers/volumes created for the proofs.
# Refuses anything whose name does not start with poc-.
for n in "$@"; do
  case "$n" in
    poc-*) docker rm -f "$n" >/dev/null 2>&1 || true ;;
    *) echo "refused: $n" ;;
  esac
done
