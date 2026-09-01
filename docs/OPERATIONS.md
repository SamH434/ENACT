# ENACT: Laws of Operation

This document defines what ENACT considers an anomaly, why, and at what
severity. It exists so operators can evaluate whether ENACT is reporting
things at the right level of concern rather than dressing up routine noise
as emergencies.

---

## 1. Purpose and scope

ENACT is a **passive, single-host, correlation-focused network observability
tool** for Windows.

- **Passive**: it observes and measures. It does not modify network state,
  probe arbitrary hosts, or perform security operations.
- **Single-host**: everything ENACT knows is from the vantage point of one
  Windows machine. Distributed inference is out of scope by design.
- **Correlation-focused**: the differentiator versus a basic reachability
  monitor is that ENACT surfaces cross-signal evidence with every event.
  Real network failures rarely present as a single signal.

ENACT is not a replacement for enterprise NPM/APM systems.

---

## 2. Signal taxonomy

ENACT operates six collectors on independent schedules. Each produces
uniformly-shaped `TelemetryRecord` objects into a shared SQLite store.
Collectors do not talk to each other. Correlation happens analytically at
the analyzer layer via time-window bucketing.

| Collector | Cadence | What it measures | Why |
|-----------|---------|------------------|-----|
| connectivity | 30s | ICMP RTT and packet loss to fixed reachability targets | Baseline reachability and latency to the public internet |
| dns | 60s | DNS resolution time per hostname, success/failure classification | Name resolution is often the first symptom of upstream trouble |
| route | 300s | `tracert` hop chain, hop count, path fingerprint | Detects path changes, which are usually benign but occasionally diagnostic |
| wifi | 120s | Current RSSI, link rate, association state, nearby AP inventory | Wireless-layer visibility, where physical problems originate |
| status | 15s | Active TCP probes to gateway, public internet, and VPN-required destinations | Fast readouts for the dashboard's current-state boxes |
| firewall | 60s | Windows Defender Firewall state per profile (Domain, Private, Public), inbound/outbound defaults | Detects local security posture changes |

**Cadences.** The rates balance data density against system load and match
the natural rate-of-change of each underlying signal. Route paths change on
the order of minutes to hours, so probing at 30s would produce mostly
duplicate fingerprints. RSSI can change per-second in motion, but a
two-minute cadence is sufficient for degradation detection at rest, which
is the dominant use case.

---

## 3. Severity levels

ENACT emits three severity levels. 

### `info`

**Definition:** A notable change occurred and may be useful context for
later diagnosis. No action is warranted and nothing is presumed wrong.

**Response expectation:** None. The event is logged for correlation and
narrative continuity. An operator scanning the log should skip these
unless correlating with a warning or critical elsewhere.

**Examples:** A route to a target changed (`route_change`). This is the
default state of the modern internet.

### `warning`

**Definition:** An observable degradation has occurred that exceeds normal
variance for the signal, but the network remains functional. The condition
may self-recover, or may be a leading indicator of a larger problem.

**Response expectation:** Notice it. Correlate with anything else happening.
If it persists or reappears, escalate attention. If it clears within a
normal recovery window, it is noted and closed.

**Examples:** RSSI dropped 15 or more dB below baseline while remaining
connected (`wifi_degradation`). DNS failure rate is elevated but below
outage threshold (`dns_outage` at warning tier).

### `critical`

**Definition:** A significant, sustained failure of a specific network
function is currently in progress. The user's ability to accomplish normal
network tasks is meaningfully impaired.

**Response expectation:** Investigate. The dashboard's alarm behavior
(screen strobe, dedicated incident window) is intentional. A real critical
event should demand attention.

**Examples:** DNS is failing for 90% or more of recent lookups (`dns_outage`
at critical tier). Traffic to reachability targets is completely dropped
and cannot be attributed to a known filter (future analyzer).

### The bar for `critical`

Not every degraded reading is a critical event. Occasional packet loss,
momentary DNS hiccups, and RSSI variance are all normal network life.
Elevating these to critical would produce alarm fatigue.

ENACT's critical tier is deliberately narrow: **sustained failure of a
whole subsystem** (all DNS, most of connectivity) rather than **any
anomaly in a subsystem**. This means ENACT will underreport compared to
a naive threshold-based system. This is deliberate.

---

## 4. Per-analyzer specifications

Each analyzer is documented with what it watches, the specific rule, the
threshold justifications, and an assessment of whether the current severity
assignment is appropriate.

### 4.1 latency_spike

**Watches:** connectivity collector's `latency_ms` metric.

**Rule:** Compute a rolling median from the last N latency samples
(baseline). Fire an event when the current sample is at least
`MULTIPLIER` times the baseline AND above `ABSOLUTE_FLOOR_MS`. 

**Current thresholds:**
- `LOOKBACK_LIMIT`: 50 samples
- `BASELINE_MIN_SAMPLES`: 10
- `MULTIPLIER`: 3.0
- `ABSOLUTE_FLOOR_MS`: 100
- `EVENT_DEBOUNCE_SEC`: 60
- **Severity:** `warning`

**Potential improvement (deferred to v2):** track spike duration. A single
spike is noise. A spike sustained across N cycles is a genuine degradation
worth escalating.

### 4.2 dns_outage

**Watches:** dns collector's `resolution_ms` metric, where `success=False`
records indicate failed lookups.

**Rule:** Over the last N DNS samples, compute the failure rate
(failed / total). Emit at severity tiers based on the rate:

- Failure rate at or above `CRITICAL_RATE`: `critical`
- Failure rate at or above `WARNING_RATE`: `warning`
- Otherwise no event

**Current thresholds:**
- `LOOKBACK_LIMIT`: 40 samples (roughly the last 40 minutes at 60s cadence)
- `WARNING_RATE`: 0.50 (50% of lookups failing)
- `CRITICAL_RATE`: 0.90 (90% of lookups failing)
- `EVENT_DEBOUNCE_SEC`: 60

**Potential improvement (deferred to v2):** distinguish system-wide DNS
failures (all hostnames failing) from targeted failures (one hostname
consistently failing). The first is upstream resolver failure. The second
is often a hostname-specific problem, such as a single site being down
rather than the resolver.

### 4.3 route_change

**Watches:** route collector's `route_fingerprint` metric.

**Rule:** For each target, if the current fingerprint differs from the
previous fingerprint for that target, emit an event. Per-target debounce
prevents multi-firing when the tracert re-runs.

**Current thresholds:**
- **Severity:** `info`

`info` is correct. Route changes on the public internet are constant,
expected, and usually benign. BGP routes reconverge, ISPs change peering,
CDN geolocation shifts. Elevating any single route change to `warning`
would be dishonest alarm generation.

One route pattern is concerning: **route flapping**, defined as multiple
route changes for the same target in a short window. This is a symptom of
upstream instability. A flapping detector would be a legitimate `warning`
and is a natural v2 addition.

### 4.4 wifi_degradation

**Watches:** wifi collector's `current_rssi_dbm` metric.

**Rule:** Compute a rolling baseline from the last N RSSI samples. Fire
when the current RSSI is at least `RSSI_DROP_DB` below the baseline AND
below `CONCERNING_RSSI_DBM`. The floor prevents firing on strong-signal
variation between -50 and -65 dBm, where the drop is real but the signal
is still excellent.

**Current thresholds:**
- `LOOKBACK_LIMIT`: 100 samples
- `BASELINE_MIN_SAMPLES`: 5
- `RSSI_DROP_DB`: 15
- `CONCERNING_RSSI_DBM`: -70
- `EVENT_DEBOUNCE_SEC`: 120
- **Severity:** `warning`

**Potential improvement (deferred to v2):** cross-signal escalation. If
Wi-Fi degrades AND ping latency spikes AND DNS starts failing, the
combination points at a specific cause (weak Wi-Fi) and warrants critical
severity as a compound event. This is the natural next step of the
correlation model.

### 4.5 firewall_disabled

**Watches:** firewall collector's `firewall_profile_state` metric.

**Rule:** For each of the three profiles (Domain, Private, Public),
compare the two most recent samples. Fire a warning event on the specific
ON to OFF transition. Per-profile debounce prevents re-firing during a
sustained-off state.

**Current thresholds:**
- `LOOKBACK_LIMIT`: 40 samples
- `EVENT_DEBOUNCE_SEC`: 300 (5 minutes)
- **Severity:** `warning`

`warning` is correct. A disabled firewall profile is a real posture change
worth investigation but not an immediate outage. The machine still
functions. Escalating to `critical` would be alarm noise, since a disabled
firewall does not mean anything is actively wrong, only that the machine
has reduced defense-in-depth. The expected response is investigation of
whether the change was deliberate, Group Policy driven, or unexpected.

**Potential improvement (deferred to v2):** detect Group Policy driven
changes specifically. The current analyzer does not distinguish
operator-initiated disables from GP-driven disables from malicious
disables. A stronger signal would correlate with recent GP application
events.

### 4.6 rogue_ap

**Watches:** wifi collector's `nearby_ap` metric.

**Rule:** For each SSID, maintain a rolling 7-day history of BSSIDs
observed advertising it. When a BSSID appears in the current wifi
collector cycle for an SSID that has prior history from other BSSIDs,
fire an info event. Per (SSID, BSSID) debounce prevents re-firing for
the same new BSSID within one hour.

**Current thresholds:**
- `LOOKBACK_LIMIT`: 2000 samples (roughly 10 wifi cycles worth of nearby_ap records)
- `BASELINE_MIN_SAMPLES`: 20 (enough history to define known-vs-new)
- `KNOWN_LOOKBACK_SEC`: 7 days
- `EVENT_DEBOUNCE_SEC`: 3600 (1 hour)
- **Severity:** `info`

`info` is the appropriate severity. The pattern this detects (a known
SSID from a new BSSID) has a substantially higher false-positive rate
than any other analyzer in ENACT. Mesh networks, roaming, corporate
deployments, and pop-up guest networks all commonly produce this pattern.

**Explicitly not implemented, and should not be:** active verification
of suspected APs (probe requests, association attempts, deauth). Passive
observation is legal and safe in every jurisdiction. Active probing of
unauthorized APs is not. This analyzer is deliberately read-only against
the storage layer.

**Potential improvement (deferred to v2):** weight events by how similar
the new BSSID's OUI (first three octets) is to the known BSSIDs.
Same-OUI new BSSIDs are highly likely to be legitimate expansion of the
same network. Different-OUI new BSSIDs are more suspicious.

---

## 5. The correlation model in practice

Every event ENACT emits carries an `evidence` dictionary. Some of that
evidence is intrinsic to the anomaly (the DNS failure rate, the RSSI drop
magnitude). Some is contextual: a snapshot of what other collectors were
reporting in the same time window.

**How to read it:**

- DNS outage with normal RSSI and normal route: probably upstream (resolver-side).
- DNS outage with elevated latency and Wi-Fi degradation: probably wireless-level, where a bad link causes everything downstream to suffer.
- Latency spike with a fresh route change in the same window: the new route is worse than the old one, suggesting the ISP shifted the path suboptimally.
- Wi-Fi degradation with no DNS or latency impact: early warning, the signal weakened but the network is still absorbing it.

The evidence panel in the incident window surfaces this so an operator
does not have to hold four charts in their head to reason about causality.

---

## 6. Security and engineering posture

ENACT's positioning as an observability tool (not a security tool) does
not mean security discipline is absent from its implementation. This
section documents the concrete engineering choices that reduce risk to
the operator running it, and the deliberate limits on what ENACT will
attempt.

### Passive-only by design

ENACT does not send unsolicited traffic to any host it observes.
Specifically:

- **No active scanning.** ENACT does not scan ports, enumerate services,
  or probe arbitrary hosts. TCP probes exist only against fixed
  reachability targets (public anycast DNS) and the host's own default
  gateway.
- **No packet injection.** ENACT does not craft, spoof, or replay network
  packets. No Scapy, no raw sockets, no monitor-mode Wi-Fi capture.
- **No offensive Wi-Fi operations.** The rogue AP detector observes what
  the OS's normal Wi-Fi scanning already sees. It does not
  deauthenticate, does not attempt to associate with suspicious APs,
  does not probe them.

This posture is a **deliberate design constraint**, not a limitation
waiting to be lifted. Active scanning has legal and ethical surface area
that varies by jurisdiction and network ownership. Keeping ENACT strictly
passive means an operator can run it on any network, including networks
they do not own such as coffee shop or hotel Wi-Fi, without risk of
misbehavior.

### Input handling

- **Parameterized SQL only.** All database queries use SQLite's parameter
  substitution (`?` placeholders). No user input is ever concatenated
  into SQL strings, eliminating SQL injection as a risk category.
- **No arbitrary path handling.** File writes (export logs, screenshot
  saves) go only to user-selected paths via native OS file dialogs.
  ENACT does not accept file paths from configuration files, network
  input, or environment variables.
- **Subprocess arguments are lists, never shell strings.** All calls to
  `netsh`, `tracert`, `ping`, `ipconfig` pass arguments as Python lists
  to `subprocess.run`, which bypasses shell interpretation entirely.
  Command injection is not possible from within ENACT's own code.

### Resource discipline

- **Bounded database growth.** Old samples, runs, and events are
  automatically pruned by the scheduler on a periodic cycle (default:
  7-day retention, pruning every hour). The database cannot grow
  unbounded.
- **Bounded work per cycle.** Each collector has a subprocess timeout
  and runs on its own interval. A slow or hung external command cannot
  block the entire system. A failing collector logs the error and
  continues on the next cycle without crashing the process.
- **Bounded query cost.** Dashboard queries use compound indexes so
  cost scales with result size, not table size. Median dashboard tick
  is under 50ms even with hundreds of thousands of stored samples (see
  stress tests in `tests/test_stress.py`).
- **Bounded UI subprocess spawning.** The TRIGGER TEST INCIDENT button
  has a hard rate limit at both the JS layer (5s cooldown) and the
  Python layer (max 3 launches per 15 seconds), preventing spam-click
  resource exhaustion of incident window subprocesses.

### What ENACT does not defend against

An honest posture also states what is out of scope:

- ENACT does not defend against a malicious operator running it on
  their own machine. Anyone who can run arbitrary Python can already
  do anything ENACT does.
- ENACT does not defend against modification of its own SQLite file.
  An attacker with write access to `data/enact.db` can alter historical
  data.
- ENACT does not defend against tampering with `netsh`, `tracert`, or
  `ipconfig` output. It trusts the OS's system tools to return truthful
  data about the machine's own state.
- ENACT does not encrypt its stored data. It runs on a single trusted
  host and is not designed for hostile-storage environments.

All points mentioned above will be deferred to v2.

---

## 7. Operator's playbook

Quick reference for interpreting the dashboard, based on observered experience in IT enviornments and personal long-term observation.

**Dashboard says INTERNET: OFFLINE, DNS ok**
- ICMP is filtered somewhere between the host and the reachability targets.
- Most common causes: VPN provider blocks ping, corporate firewall blocks
  outbound ICMP.
- Not an emergency in itself. If browsing works, this is a measurement
  limitation rather than a network failure.

**Dashboard says INTERNET: OFFLINE**
- Both DNS and ping fail. This is a genuine reachability problem.
- Check: is Wi-Fi connected? Is the ISP up? Is a VPN connected but with
  a broken tunnel?

**A critical DNS outage fires**
- 90% or more of DNS lookups are failing over the last 40 minutes.
- Probable causes in order: resolver misconfigured, ISP DNS server down,
  DNS-over-HTTPS misroute, security appliance blocking outbound port 53.
- Check the concurrent samples in the incident window. If connectivity
  is also failing, this is a broader outage rather than a DNS-specific
  problem.

**A latency spike warning fires**
- One sample is 3x the recent median and above 100ms.
- Usually transient. Check whether it self-recovers in the next cycle.
- If it repeats within an hour, correlate with route changes and Wi-Fi
  state.

**A Wi-Fi degradation warning fires**
- RSSI dropped 15 or more dB and is now below -70 dBm.
- Probable causes: physical distance increased from the AP, interference
  from a nearby device, a neighboring AP started broadcasting on the same
  channel.
- If DNS and latency stay clean, the wireless link is coping. If they
  degrade too, the degradation has crossed into operational impact.

**A route change info event fires**
- A route was reconfigured. This is normal internet behavior.
- Only worth attention if the same target flips fingerprints repeatedly
  (flapping).

