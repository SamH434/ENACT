"""
Wi-Fi collector - current link state + nearby AP scan via netsh.
"""

import re
import subprocess

from src.collectors.base import Collector
from src.utils.records import TelemetryRecord