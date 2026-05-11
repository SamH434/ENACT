"""
ENACT (Engine for Network Anomaly, Condition, and Telemetry)

This file will have to verify each package imports cleanly, as in
for each subsystem and confirms the skeletion is "alive", this is here for 
intital testing.
"""

from src.utils.logger import get_logger
from src import collectors, analyzers, dashboard, storage  # noqa: F401
# this is here because linter keeps flagging unused imports

log = get_logger("enact.main")


def main() -> None:
    log.info("ENACT starting up")
    log.info("subsystem loaded: collectors")
    log.info("subsystem loaded: analyzers")
    log.info("subsystem loaded: storage")
    log.info("subsystem loaded: dashboard")
    log.info("ENACT alive: skeleton OK")


if __name__ == "__main__":
    main()