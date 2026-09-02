#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Exam Cram Coach command line. Run `python coach.py help`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from coach.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
