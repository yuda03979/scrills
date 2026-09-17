"""
---
name: broken
description: Deliberately dirty, to prove default toolchains stay out of .scrills.
version: 0.1.0
---
"""
import definitely_not_a_real_package
import os
import sys as unused_alias

from .missing_sibling import nothing


def bad(x:int)->str:
    y = definitely_not_a_real_package.call( nothing ,x)
    return y
