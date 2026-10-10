"""Deliberately dirty imports and bad() to test default toolchain exclusions."""
import definitely_not_a_real_package
import os
import sys as unused_alias

from .missing_sibling import nothing


def bad(x:int)->str:
    y = definitely_not_a_real_package.call( nothing ,x)
    return y
