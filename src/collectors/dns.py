"""
DNS collector - resolves a list of hostnames and times each lookup.

Uses Python's socket module directly rather than spawning nslookup, 
stdlib does the heavy lifting.

FYI: socket.getaddrinfo uses whatever DNS resolver the OS is configured to
use (router, ISP, or whatever's set manually). We tag records as 'resolver:
system' since we don't know which one actually answered without parsing
ipconfig. Probing specific resolvers (1.1.1.1 vs 8.8.8.8) would need a
library like dnspython, future enhancement.
"""

import socket
import time

from src.collectors.base import Collector
from src.utils.records import TelemetryRecord


# common, well distributed hostnames
DEFAULT_HOSTNAMES = [
    "google.com",
    "cloudflare.com",
    "github.com",
    "wikipedia.org",
]
DEFAULT_TIMEOUT_SEC = 3.0

"""
Resolves a list of hostnames each cycle and records resolution time per lookup.

Failures (NXDOMAIN, timeout, network down) are recorded explicitly with
value=None and a reason in metadata, rather than crashing or pretending the
lookup was instant. This matters for Phase 4: averaging over None skips the
failure, averaging over 0 would silently corrupt the result.
"""
class DNSCollector(Collector):
    name = "dns"

    # stores config (hostnames, timeout) with sensible defaults for any unset values
    def __init__(
        self,
        hostnames: list[str] | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        
        super().__init__()
        self.hostnames = hostnames or DEFAULT_HOSTNAMES
        self.timeout_sec = timeout_sec

    # runs one DNS lookup across every configured hostname
    def collect(self) -> list[TelemetryRecord]:
        records: list[TelemetryRecord] = []
        for hostname in self.hostnames:
            records.append(self._resolve_one(hostname))
        return records

    # resolves a single hostname, times it, and returns one record (success or failure)
    def _resolve_one(self, hostname: str) -> TelemetryRecord:
        """Resolve one hostname and time it. Records success or failure."""
        # socket has a global default timeout. Set it locally for this lookup.
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(self.timeout_sec)

        start = time.perf_counter()
        try:
            # getaddrinfo returns a list of address tuples, don't really care about
            # the addresses themselves, only that the lookup succeeded and how long it took.
            results = socket.getaddrinfo(hostname, None)
            # For above, I chose socket.getaddrinfo instead of socket.gethostbyname
            # Both do DNS lookups, but getaddrinfo supports IPv6 and returns more info
            elapsed_ms = (time.perf_counter() - start) * 1000
            # I used None instead of 0 since we average out latencies and 0 should pull the average down
            ip_address = results[0][4][0] if results else None

            return TelemetryRecord(
                collector=self.name,
                metric="resolution_ms",
                value=round(elapsed_ms, 2),
                metadata={
                    "hostname": hostname,
                    "resolver": "system",
                    "resolved_ip": ip_address,
                    "success": True,
                },
            )
        # gaierror covers NXDOMAIN, refused, no network, etc.
        except socket.gaierror as e:
            # highest resolution clock Python offers and is monotonic (never goes backwards)
            # even if system clock gets adjusted. This is for measuring short durations
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.log.warning("DNS resolution failed for %s: %s", hostname, e)
            return TelemetryRecord(
                collector=self.name,
                metric="resolution_ms",
                value=None,
                metadata={
                    "hostname": hostname,
                    "resolver": "system",
                    "success": False,
                    "error": str(e),
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )
        except socket.timeout:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.log.warning("DNS resolution timed out for %s", hostname)
            return TelemetryRecord(
                collector=self.name,
                metric="resolution_ms",
                value=None,
                metadata={
                    "hostname": hostname,
                    "resolver": "system",
                    "success": False,
                    "error": "timeout",
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )
        # finally block always restores the global socket timeout, even if an exception fired.
        # forgetting this would leak the short timeout to other parts of the program
        finally:
            socket.setdefaulttimeout(old_timeout)


if __name__ == "__main__":
    import json

    collector = DNSCollector()
    for record in collector.collect():
        print(json.dumps(record.to_dict(), indent=2))