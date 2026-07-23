"""
Parser tests: verify collectors correctly extract data from subprocess output
"""

PING_OUTPUT_SUCCESS = """
Pinging 1.1.1.1 with 32 bytes of data:
Reply from 1.1.1.1: bytes=32 time=8ms TTL=57
Reply from 1.1.1.1: bytes=32 time=11ms TTL=57
Reply from 1.1.1.1: bytes=32 time=9ms TTL=57
Reply from 1.1.1.1: bytes=32 time=14ms TTL=57

Ping statistics for 1.1.1.1:
    Packets: Sent = 4, Received = 4, Lost = 0 (0% loss),
Approximate round trip times in milli-seconds:
    Minimum = 8ms, Maximum = 14ms, Average = 11ms
"""

PING_OUTPUT_100_LOSS = """
Pinging 192.0.2.1 with 32 bytes of data:
Request timed out.
Request timed out.
Request timed out.
Request timed out.

Ping statistics for 192.0.2.1:
    Packets: Sent = 4, Received = 0, Lost = 4 (100% loss),
"""


class TestConnectivityCollectorParsing:

    def test_parse_success_yields_latency_and_loss(self):
        """A healthy ping produces both a packet_loss and a latency record."""
        from src.collectors.connectivity import ConnectivityCollector

        collector = ConnectivityCollector()
        records = collector._parse_ping_output("1.1.1.1", PING_OUTPUT_SUCCESS)

        metrics = {r.metric for r in records}
        assert "packet_loss_pct" in metrics
        assert "latency_ms" in metrics

        loss = next(r for r in records if r.metric == "packet_loss_pct")
        assert loss.value == 0.0
        assert loss.metadata["target"] == "1.1.1.1"

        latency = next(r for r in records if r.metric == "latency_ms")
        assert latency.value == 11  # average
        assert latency.metadata["min_ms"] == 8
        assert latency.metadata["max_ms"] == 14
        assert latency.metadata["jitter_ms"] == 6  # max - min

    def test_parse_total_loss_produces_only_loss_record(self):
        """100% loss produces a loss record but no latency record."""
        from src.collectors.connectivity import ConnectivityCollector

        collector = ConnectivityCollector()
        records = collector._parse_ping_output("192.0.2.1", PING_OUTPUT_100_LOSS)

        loss_records = [r for r in records if r.metric == "packet_loss_pct"]
        latency_records = [r for r in records if r.metric == "latency_ms"]

        assert len(loss_records) == 1
        assert loss_records[0].value == 100.0
        assert len(latency_records) == 0

    def test_parse_unparseable_output_yields_unreachable_record(self):
        """Garbage input should produce a 100% loss record, not crash."""
        from src.collectors.connectivity import ConnectivityCollector

        collector = ConnectivityCollector()
        records = collector._parse_ping_output("192.0.2.1", "not a ping output")

        assert len(records) == 1
        assert records[0].metric == "packet_loss_pct"
        assert records[0].value == 100.0
        assert records[0].metadata.get("unreachable") is True


IPCONFIG_WITH_VPN = """
Windows IP Configuration

   Host Name . . . . . . . . . . . . : SAMPC
   Primary Dns Suffix  . . . . . . . :
   Node Type . . . . . . . . . . . . : Hybrid

Unknown adapter LetsTAP:

   Connection-specific DNS Suffix  . :
   Description . . . . . . . . . . . : TAP-Windows Adapter V9
   Physical Address. . . . . . . . . : 00-FF-11-22-33-44
   DHCP Enabled. . . . . . . . . . . : No
   IPv4 Address. . . . . . . . . . . : 26.26.26.1(Preferred)
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . :

Wireless LAN adapter Wi-Fi:

   Connection-specific DNS Suffix  . :
   Description . . . . . . . . . . . : Intel(R) Wireless-AC 8265
   Physical Address. . . . . . . . . : AA-BB-CC-DD-EE-FF
   DHCP Enabled. . . . . . . . . . . : Yes
   IPv4 Address. . . . . . . . . . . : 192.168.0.11(Preferred)
"""

IPCONFIG_NO_VPN = """
Windows IP Configuration

Wireless LAN adapter Wi-Fi:

   Connection-specific DNS Suffix  . :
   Description . . . . . . . . . . . : Intel(R) Wireless-AC 8265
   Physical Address. . . . . . . . . : AA-BB-CC-DD-EE-FF
   DHCP Enabled. . . . . . . . . . . : Yes
   IPv4 Address. . . . . . . . . . . : 192.168.0.11(Preferred)

Ethernet adapter Ethernet:

   Media State . . . . . . . . . . . : Media disconnected
"""


class TestStatusCollectorTunnelDetection:

    def test_detects_tunnel_when_letstap_present(self, monkeypatch):
        """Given ipconfig output with LetsTAP + TAP-Windows description, tunnel present."""
        from src.collectors.status import StatusCollector
        import subprocess

        class FakeResult:
            stdout = IPCONFIG_WITH_VPN
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

        collector = StatusCollector()
        assert collector._tunnel_adapter_present() is True

    def test_no_tunnel_when_only_physical_adapters(self, monkeypatch):
        """Given ipconfig with only Wi-Fi + Ethernet, no tunnel present."""
        from src.collectors.status import StatusCollector
        import subprocess

        class FakeResult:
            stdout = IPCONFIG_NO_VPN
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

        collector = StatusCollector()
        assert collector._tunnel_adapter_present() is False

    def test_is_tunnel_matches_by_description(self):
        """_is_tunnel matches the tap-windows hint from description, not just name."""
        from src.collectors.status import StatusCollector

        collector = StatusCollector()
        hints = ["tap-windows", "wireguard", "openvpn"]
        physical = ["wi-fi", "ethernet"]

        assert collector._is_tunnel(
            name="Unknown adapter LetsTAP",
            desc="TAP-Windows Adapter V9",
            hints=hints, physical=physical,
        ) is True

    def test_is_tunnel_excludes_physical_adapters(self):
        """A Wi-Fi adapter should never be classified as a tunnel even if its
        description happens to contain a matching hint (edge case protection)."""
        from src.collectors.status import StatusCollector

        collector = StatusCollector()
        assert collector._is_tunnel(
            name="Wireless LAN adapter Wi-Fi",
            desc="Some VPN-branded Wi-Fi driver",
            hints=["vpn"], physical=["wi-fi"],
        ) is False

NETSH_ADVFIREWALL_ALL_ENABLED = """
Domain Profile Settings:
----------------------------------------------------------------------
State                                 ON
Firewall Policy                       BlockInbound,AllowOutbound
LocalFirewallRules                    N/A
LocalConSecRules                      N/A

Private Profile Settings:
----------------------------------------------------------------------
State                                 ON
Firewall Policy                       BlockInbound,AllowOutbound
LocalFirewallRules                    N/A

Public Profile Settings:
----------------------------------------------------------------------
State                                 ON
Firewall Policy                       BlockInbound,AllowOutbound
LocalFirewallRules                    N/A
"""

NETSH_ADVFIREWALL_PUBLIC_DISABLED = """
Domain Profile Settings:
----------------------------------------------------------------------
State                                 ON
Firewall Policy                       BlockInbound,AllowOutbound

Private Profile Settings:
----------------------------------------------------------------------
State                                 ON
Firewall Policy                       BlockInbound,AllowOutbound

Public Profile Settings:
----------------------------------------------------------------------
State                                 OFF
Firewall Policy                       BlockInbound,AllowOutbound
"""


class TestFirewallCollectorParsing:

    def test_parses_all_three_profiles(self):
        """Every profile with a State line shows up in the parsed output."""
        from src.collectors.firewall import FirewallCollector

        collector = FirewallCollector()
        profiles = collector._parse_profiles(NETSH_ADVFIREWALL_ALL_ENABLED)

        assert set(profiles.keys()) == {"Domain", "Private", "Public"}
        assert profiles["Domain"]["state"] == "ON"
        assert profiles["Private"]["state"] == "ON"
        assert profiles["Public"]["state"] == "ON"

    def test_parses_mixed_states(self):
        """Correctly reports Public OFF while Domain and Private are ON."""
        from src.collectors.firewall import FirewallCollector

        collector = FirewallCollector()
        profiles = collector._parse_profiles(NETSH_ADVFIREWALL_PUBLIC_DISABLED)

        assert profiles["Domain"]["state"] == "ON"
        assert profiles["Private"]["state"] == "ON"
        assert profiles["Public"]["state"] == "OFF"

    def test_parses_firewall_policy(self):
        """Extracts BlockInbound/AllowOutbound from the policy line."""
        from src.collectors.firewall import FirewallCollector

        collector = FirewallCollector()
        profiles = collector._parse_profiles(NETSH_ADVFIREWALL_ALL_ENABLED)

        assert profiles["Domain"]["inbound"] == "BlockInbound"
        assert profiles["Domain"]["outbound"] == "AllowOutbound"

    def test_collect_emits_summary_record(self, monkeypatch):
        """collect() produces a firewall_summary record with enabled count."""
        from src.collectors.firewall import FirewallCollector
        import subprocess

        class FakeResult:
            stdout = NETSH_ADVFIREWALL_ALL_ENABLED
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

        records = FirewallCollector().collect()
        summary_records = [r for r in records if r.metric == "firewall_summary"]
        assert len(summary_records) == 1
        assert summary_records[0].value == 3

    def test_collect_summary_reflects_partial_disable(self, monkeypatch):
        """One profile disabled -> summary count is 2 out of 3."""
        from src.collectors.firewall import FirewallCollector
        import subprocess

        class FakeResult:
            stdout = NETSH_ADVFIREWALL_PUBLIC_DISABLED
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

        records = FirewallCollector().collect()
        summary = next(r for r in records if r.metric == "firewall_summary")
        assert summary.value == 2

    def test_collect_returns_unavailable_when_netsh_fails(self, monkeypatch):
        """If netsh isn't available, we get an unavailable record, not a crash."""
        from src.collectors.firewall import FirewallCollector
        import subprocess

        def fake_run(*a, **kw):
            raise FileNotFoundError("netsh not found")
        monkeypatch.setattr(subprocess, "run", fake_run)

        records = FirewallCollector().collect()
        assert len(records) == 1
        assert records[0].metadata.get("unavailable") is True