"""
ENACT (Engine for Network Anomaly, Condition, and Telemetry)

Real entrypoint. Builds the four collectors, registers them with the scheduler
at their own intervals, and runs until Ctrl+C.

Intervals are deliberately different to match each collector's cost:
    connectivity: frequent, it's cheap and the most time-sensitive signal
    dns:          frequent-ish, also cheap
    wifi:         moderate, netsh scans take a couple seconds
    route:        rare, tracert is slow and routes don't change often
"""

from src.collectors.connectivity import ConnectivityCollector
from src.collectors.dns import DNSCollector
from src.collectors.route import RouteCollector
from src.collectors.wifi import WifiCollector
from src.collectors.status import StatusCollector
from src.collectors.firewall import FirewallCollector

from src.analyzers.latency_spike import LatencySpikeAnalyzer
from src.analyzers.dns_outage import DNSOutageAnalyzer
from src.analyzers.route_change import RouteChangeAnalyzer
from src.analyzers.wifi_degradation import WifiDegradationAnalyzer
from src.analyzers.rogue_ap import RogueAPAnalyzer

from src.analyzers.firewall_disabled import FirewallDisabledAnalyzer
from src.scheduler import Scheduler
from src.utils.logger import get_logger

log = get_logger("enact.main")

# wires up the collectors and their intervals, then runs the scheduler forever
def main() -> None:
    log.info("ENACT starting up")

    scheduler = Scheduler(retention_days=7)

    scheduler.add(ConnectivityCollector(), interval_sec=30)
    scheduler.add(DNSCollector(), interval_sec=60)
    scheduler.add(WifiCollector(), interval_sec=120)
    scheduler.add(RouteCollector(), interval_sec=300)
    scheduler.add(StatusCollector(), interval_sec=15)
    scheduler.add(FirewallCollector(), interval_sec=60)
    scheduler.add_analyzer(LatencySpikeAnalyzer(), interval_sec=30)
    scheduler.add_analyzer(DNSOutageAnalyzer(), interval_sec=30)
    scheduler.add_analyzer(RouteChangeAnalyzer(), interval_sec=60)
    scheduler.add_analyzer(WifiDegradationAnalyzer(), interval_sec=60)
    scheduler.add_analyzer(FirewallDisabledAnalyzer(), interval_sec=60)
    scheduler.add_analyzer(RogueAPAnalyzer(), interval_sec=120)

    log.info("collectors registered, entering run loop (Ctrl+C to stop)")
    scheduler.run_forever()

if __name__ == "__main__":
    main()