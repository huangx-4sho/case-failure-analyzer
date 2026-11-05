#!/usr/bin/env python3
"""
Shared argparse validators (`scripts/cli_args.py`)

Keeps CLI input contracts in one place so every entry point rejects the same
bad values with the same exit code (argparse exits 2) instead of degrading
silently. In particular `--max-log-bytes` must be strictly positive: zero or a
negative value would otherwise make head/tail slicing read an unbounded log.
"""

from __future__ import annotations

import argparse


def positive_int(value: str) -> int:
    """argparse type: require a strictly positive integer."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value!r}") from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {parsed}")
    return parsed
