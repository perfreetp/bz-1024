#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""园区绿植巡检命令行工具入口"""
import os
import sys

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from greeninspect.cli import main

if __name__ == "__main__":
    main()
