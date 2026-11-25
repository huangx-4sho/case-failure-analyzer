"""Test suite package for case-failure-analyzer."""

# Test jobs use the in-process reviewer. Never start an authenticated model CLI in unit tests.
import os

os.environ["CFA_DEEP_REVIEW_MODE"] = "host"
