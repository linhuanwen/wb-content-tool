"""Rename project folder from 电商采集 to DianShangCaiJi."""
import os
import sys
import time

# Wait for caller to exit (release directory lock)
time.sleep(4)

old = r"d:\电商采集"
new = r"d:\DianShangCaiJi"

if not os.path.isdir(old):
    print(f"Source not found: {old}")
    sys.exit(1)

try:
    os.rename(old, new)
    print(f"OK: {old} -> {new}")
except OSError as e:
    print(f"FAIL: {e}")
