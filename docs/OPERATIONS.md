# ENACT: Laws of Operation

This document defines what ENACT considers an anomaly, why, and at what
severity. It exists so operators (and reviewers) can evaluate whether ENACT
is reporting things at the right level of concern rather than dressing up
routine noise as emergencies.

The correctness of a monitoring tool isn't in how impressive it looks. It's
in whether a network engineer glancing at the dashboard would agree that
what's flagged deserves the level of attention it's getting.

---

## 1. Purpose and scope

ENACT is a **passive, single-host, correlation-focused network observability
tool** for Windows.

- **Passive**: it observes and measures, it does not modify network state,
  probe arbitrary hosts, or perform security operations.
- **Single-host**: everything ENACT knows is from the vantage point of one
  Windows machine. Distributed inference is out of scope by design.
- **Correlation-focused**: the differentiator versus a basic "ping and DNS"
  monitor is that ENACT surfaces cross-signal evidence with every event,
  because real network failures rarely present as a single signal.
 
ENACT is NOT a replacement for enterprise NPM/APM systems

---

## 2. Signal taxonomy

ENACT operates five collectors on independent schedules. Each produces
uniformly-shaped `TelemetryRecord` objects into a shared SQLite store.
Collectors do not talk to each other; correlation happens analytically at
the analyzer layer via time-window bucketing.

| Collector | Cadence | What it measures | Why |
|-----------|---------|------------------|-----|
| connectivity | 30s | ICMP RTT and packet loss to fixed reachability targets | Baseline "can we reach the internet, and how fast" |
| dns | 60s | DNS resolution time per hostname, success/failure classification | Name resolution is often the first symptom of upstream trouble |
| route | 300s | `tracert` hop chain, hop count, path fingerprint | Detects path changes, which are usually benign but occasionally diagnostic |
| wifi | 120s | Current RSSI, link rate, association state, nearby AP inventory | Wireless-layer visibility, where physical problems originate |
| status | 15s | Wi-Fi association, composite internet reachability, VPN adapter presence | Fast "is the network working right now" readouts for the dashboard |
| firewall | 60s | Windows Defender Firewall state per profile (Domain/Private/Public), inbound/outbound defaults | Detects local security posture changes |

**A note on cadences.** The rates were chosen to balance data density with
system load and to match the natural rate-of-change of the underlying signal.
Route paths change on the order of minutes to hours, so probing at 30s
would produce mostly duplicate fingerprints. RSSI can change per-second in
motion, but a 2-minute cadence is sufficient for degradation detection at
rest, which is the dominant use case.

---

## 3. Severity levels: definitions

ENACT emits three severity levels. These definitions are precise on purpose;
severity inflation destroys the value of a monitoring tool.

### `info`

**Definition:** A notable change occurred, and it may be useful context for
later diagnosis, but no action is warranted and nothing is presumed wrong.

**Response expectation:** None. The event is logged for correlation and
narrative continuity. An engineer scanning the log should mentally skip
these unless correlating with a warning or critical elsewhere.

**Examples:** A route to a target changed (`route_change`). This is the
default state of the modern internet.

### `warning`

**Definition:** An observable degradation has occurred that exceeds normal
variance for the signal, but the network remains functional. The condition
may self-recover, or may be a leading indicator of a larger problem.

**Response expectation:** Notice it. Correlate mentally with anything else
happening. If it persists or reappears, escalate attention. If it clears
within a normal recovery window, it's noted and closed.

**Examples:** RSSI dropped 15+ dB below baseline while remaining connected
(`wifi_degradation`). DNS failure rate is elevated but below outage
threshold (`dns_outage` at warning tier).

### `critical`

**Definition:** A significant, sustained failure of a specific network
function is currently in progress. The user's ability to accomplish
normal network tasks is meaningfully impaired.

**Response expectation:** Investigate. This is not a "check when convenient"
event; the dashboard's alarm behavior (screen strobe, dedicated incident
window) is intentional because a real critical event should demand
attention.

**Examples:** DNS is failing for 90%+ of recent lookups
(`dns_outage` at critical tier). Traffic to reachability targets is
completely dropped and cannot be attributed to a known filter
(future analyzer).

### The bar for `critical` (making sure things are realistic)

Not every "bad reading" is a critical event. A network engineer knows that
occasional packet loss, momentary DNS hiccups, and RSSI variance are all
normal network life. Elevating these to critical would be crying wolf.

ENACT's critical tier is deliberately narrow: **sustained failure of a
whole subsystem** (all DNS, most of connectivity) rather than **any anomaly
in a subsystem**. This means ENACT will underreport compared to a naive
threshold-based system. That's the point.

---

## 4. Per-analyzer specifications

Each analyzer is documented with: what it watches, the specific rule, the
threshold justifications, and an honesty check on whether the current
severity assignment is appropriate.

### 4.1 latency_spike

**Watches:** connectivity collector's `latency_ms` metric.

**Rule:** Compute a rolling median from the last N latency samples (baseline).
Fire an event when the current sample is at least `MULTIPLIER × baseline`
AND above `ABSOLUTE_FLOOR_MS`. The floor prevents firing on "5ms -> 15ms"
noise, which is a 3x multiplier but not operationally meaningful.

**Current thresholds:**
- `LOOKBACK_LIMIT`: 50 samples
- `BASELINE_MIN_SAMPLES`: 10
- `MULTIPLIER`: 3.0
- `ABSOLUTE_FLOOR_MS`: 100
- `EVENT_DEBOUNCE_SEC`: 60
- **Severity: `warning`**

**Honesty check:** The `warning` severity is honest and appropriate. A 3x
latency spike above a 100ms floor is a real deviation but does not mean the
network is broken; most such events are single-packet queueing spikes at
some intermediate hop. Escalating this to `critical` would produce alarm
noise.

**Potential improvement (deferred to v2):** Track spike *duration*. A single
spike is noise; a spike sustained across N cycles is a genuine degradation
worth escalating.

### 4.2 dns_outage

**Watches:** dns collector's `resolution_ms` metric, where `success=False`
records indicate failed lookups.

**Rule:** Over the last N DNS samples, compute the failure rate
(failed / total). Emit at severity tiers based on the rate:

- Failure rate ≥ `CRITICAL_RATE` -> `critical`
- Failure rate ≥ `WARNING_RATE` -> `warning`
- Otherwise no event

**Current thresholds:**
- `LOOKBACK_LIMIT`: 40 samples (roughly the last 40 minutes at 60s cadence)
- `WARNING_RATE`: 0.50 (50% of lookups failing)
- `CRITICAL_RATE`: 0.90 (90% of lookups failing)
- `EVENT_DEBOUNCE_SEC`: 60

**Honesty check:** These thresholds are defensible. A 50% failure rate
over 40 lookups is not "one bad minute" (genuine multi-minute
resolution problem). A 90% failure rate is functionally an outage; nothing
resolves. `critical` is the honest severity for the 90% tier.

**Potential improvement (deferred to v2):** Distinguish *system-wide* DNS
failures (all hostnames failing) from *targeted* failures (one hostname
consistently failing). The first is upstream resolver failure; the second
is often a hostname-specific problem (e.g. a specific site is down, not
your resolver).

### 4.3 route_change

**Watches:** route collector's `route_fingerprint` metric.

**Rule:** For each target, if the current fingerprint differs from the
previous fingerprint for that target, emit an event. Per-target debounce
prevents multi-firing when the tracert re-runs.

**Current thresholds:**
- **Severity: `info`**

**Honesty check:** This is correct. Route changes on the public internet are
constant, expected, and usually benign. BGP routes reconverge, ISPs change
peering, CDN geolocation shifts (all routine). Elevating any single route
change to `warning` would be dishonest alarm-generation.

**However**, one route pattern *is* concerning: **route flapping**, defined
as N+ route changes for the same target in a short window. This is a
symptom of upstream instability. A flapping detector would be a legitimate
`warning` and is a natural v2 addition.

### 4.4 wifi_degradation

**Watches:** wifi collector's `current_rssi_dbm` metric.

**Rule:** Compute a rolling baseline from the last N RSSI samples. Fire
when the current RSSI is at least `RSSI_DROP_DB` below baseline AND below
`CONCERNING_RSSI_DBM`. The "below concerning" floor prevents firing on
strong-signal noise like -50 to -65, where the drop is real but the signal
is still excellent.

**Current thresholds:**
- `LOOKBACK_LIMIT`: 100 samples
- `BASELINE_MIN_SAMPLES`: 5
- `RSSI_DROP_DB`: 15
- `CONCERNING_RSSI_DBM`: -70
- `EVENT_DEBOUNCE_SEC`: 120
- **Severity: `warning`**

**Honesty check:** Both thresholds are defensible. A 15 dB drop is meaningful
(the received signal is ~32x weaker on a linear scale). The -70 dBm floor is
the industry consensus for "connection is stable but noticeable", below it,
retransmissions and rate downshifts start. `warning` is correct because
Wi-Fi can degrade without the user losing connectivity.

**Potential improvement (deferred to v2):** Cross-signal escalation. If Wi-Fi
degrades AND ping latency spikes AND DNS starts failing, that combination
points at a specific cause (weak Wi-Fi) and warrants critical severity as
a compound event. This is the natural next step of the correlation model.

### 4.5 firewall_disabled

**Watches:** firewall collector's `firewall_profile_state` metric.

**Rule:** For each of the three profiles (Domain, Private, Public), compare
the two most recent samples. Fire a warning event on the specific ON -> OFF
transition. Per-profile debounce prevents re-firing during a sustained-off
state.

**Current thresholds:**
- `LOOKBACK_LIMIT`: 40 samples
- `EVENT_DEBOUNCE_SEC`: 300 (5 minutes)
- **Severity: `warning`**

**Honesty check:** `warning` is correct here. A disabled firewall profile is a
real posture change worth investigation but not an immediate outage; the
machine still functions. Escalating to `critical` would be alarm noise, since
disabled firewall doesn't mean anything is wrong *right now*, only that the
machine has reduced defense-in-depth. The operator's expected response is
"investigate: was this deliberate, Group Policy, or something else?"

**Potential improvement (deferred to v2):** Detect Group Policy-driven changes
specifically. Right now the analyzer doesn't distinguish operator-initiated
disables from GP-driven disables from malicious disables. A stronger signal
would correlate with recent GP application events.

### 4.6 rogue_ap

**Watches:** wifi collector's `nearby_ap` metric.

**Rule:** For each SSID, maintain a rolling 7-day history of BSSIDs observed
advertising it. When a BSSID appears in the current wifi collector cycle for
an SSID that has prior history from *other* BSSIDs, fire an info event.
Per-(SSID, BSSID) debounce prevents re-firing for the same new BSSID within
one hour.

**Current thresholds:**
- `LOOKBACK_LIMIT`: 2000 samples (~10 wifi cycles worth of nearby_ap records)
- `BASELINE_MIN_SAMPLES`: 20 (need enough history to define "known")
- `KNOWN_LOOKBACK_SEC`: 7 days
- `EVENT_DEBOUNCE_SEC`: 3600 (1 hour)
- **Severity: `info`**

**Honesty check:** `info` is the honest severity. The pattern this detects
("known SSID from new BSSID") has a substantially higher false-positive rate
than any other analyzer in ENACT. Mesh networks, roaming, corporate
deployments, and pop-up guest networks all produce this pattern legitimately.
Escalating to warning would produce alarm fatigue that would poison the
signal. Info-severity means the event is logged for later review by a human
who can distinguish "my new mesh node came online" from "someone in the coffee
shop is running an evil twin."

**Explicitly not implemented (and shouldn't be):** active verification of
suspected APs (probe requests, association attempts, deauth). Passive
observation is legal and safe in every jurisdiction; active probing of
unauthorized APs is not. This analyzer is deliberately read-only against the
storage layer.

**Potential improvement (deferred to v2):** Weight events by how similar the
new BSSID's OUI (first three octets) is to the known BSSIDs. Same-OUI new
BSSIDs are highly likely to be legitimate expansion of the same network;
different-OUI new BSSIDs are more suspicious.

---

## 5. The correlation model in practice

Every event ENACT emits carries an `evidence` dictionary. Some of that
evidence is intrinsic to the anomaly (the DNS failure rate, the RSSI drop
magnitude). Some is *contextual*, essentially a snapshot of what other collectors
were reporting in the same time window.

**How to read it as an operator:**

- **DNS outage with normal RSSI and normal route** -> probably upstream
  (resolver-side).
- **DNS outage with elevated latency and Wi-Fi degradation** -> probably
  wireless-level (your link is bad, everything downstream suffers).
- **Latency spike with a fresh route change in the same window** -> the new
  route is worse than the old one; the ISP shifted your path suboptimally.
- **Wi-Fi degradation with no DNS or latency impact** -> early warning; your
  signal weakened but the network is still absorbing it.

The evidence panel in the incident window surfaces this so an operator
doesn't have to hold four charts in their head to reason about causality.

---

## 6. Security and engineering posture

ENACT's positioning as an observability tool (not a security tool) doesn't
mean security discipline is absent from its implementation. This section
documents the concrete engineering choices that reduce risk to the operator
running it, and the deliberate limits on what ENACT will attempt.

### Passive-only by design

ENACT does not send unsolicited traffic to any host it observes. Specifically:

- **No active scanning.** ENACT does not scan ports, enumerate services, or
  probe arbitrary hosts. TCP probes exist only against fixed reachability
  targets (public anycast DNS) and the host's own default gateway.
- **No packet injection.** ENACT does not craft, spoof, or replay network
  packets. No Scapy, no raw sockets, no monitor-mode Wi-Fi capture.
- **No offensive Wi-Fi operations.** The rogue AP detector observes what the
  OS's normal Wi-Fi scanning already sees. It does not deauthenticate, does
  not attempt to associate with suspicious APs, does not probe them.

This posture is a **deliberate design constraint**, not a limitation waiting
to be lifted. Active scanning has legal and ethical surface area
that varies by jurisdiction and network ownership. Keeping ENACT strictly
passive means an operator can run it on any network (including networks
they don't own, like a coffee shop or hotel Wi-Fi) without risk of
misbehavior.

### Input handling

- **Parameterized SQL only.** All database queries use SQLite's parameter
  substitution (`?` placeholders). No user input is ever concatenated into
  SQL strings, eliminating SQL injection as a risk category.
- **No arbitrary path handling.** File writes (export logs, screenshot
  saves) go only to user-selected paths via native OS file dialogs. ENACT
  does not accept file paths from configuration files, network input, or
  environment variables.
- **Subprocess arguments are lists, never shell strings.** All calls to
  `netsh`, `tracert`, `ping`, `ipconfig` pass arguments as Python lists to
  `subprocess.run`, which bypasses shell interpretation entirely. Command
  injection is not possible from within ENACT's own code.

### Resource discipline

- **Bounded database growth.** Old samples, runs, and events are
  automatically pruned by the scheduler on a periodic cycle (default: 7-day
  retention, pruning every hour). The database cannot grow unbounded.
- **Bounded work per cycle.** Each collector has a subprocess timeout and
  runs on its own interval; a slow or hung external command cannot block
  the entire system. A failing collector logs the error and continues on
  the next cycle without crashing the process.
- **Bounded query cost.** Dashboard queries use compound indexes so cost
  scales with result size, not table size. Median dashboard tick is under
  50ms even with hundreds of thousands of stored samples (see stress tests
  in `tests/test_stress.py`).
- **Bounded UI subprocess spawning.** The TRIGGER TEST INCIDENT button
  has a hard rate limit at both the JS layer (5s cooldown) and the Python
  layer (max 3 launches per 15 seconds), preventing spam-click resource
  exhaustion of incident window subprocesses.

### What ENACT does NOT try to defend against

An honest posture also states what's out of scope:

- ENACT does not defend against a malicious operator running it on their
  own machine. If you can run arbitrary Python, you can already do anything
  ENACT does.
- ENACT does not defend against modification of its own SQLite file. An
  attacker with write access to `data/enact.db` can alter historical data.
- ENACT does not defend against tampering with `netsh` / `tracert` /
  `ipconfig` output. It trusts the OS's system tools to return truthful
  data about the machine's own state.
- ENACT does not encrypt its stored data. It runs on a single trusted host
  and is not designed for hostile-storage environments.

These are not oversights — they're the correct scope decisions for a
single-host observability tool. Defending against a compromised host is
what an EDR or SIEM does, and mixing scope would produce a worse version
of both.

## 7. Known blind spots and honest limitations

A truthful spec includes what the tool *cannot* see.

**ENACT cannot detect:**

- **ICMP-filtered environments** (VPNs, corporate networks). Ping failures
  in these environments are not "network broken" but "network dropping
  probes." ENACT shows this honestly via the `NO DATA ICMP BLOCKED`
  overlay rather than pretending to have measured what it can't.
- **Application-layer issues.** HTTPS handshake failures, TLS certificate
  errors, application timeouts, ENACT does not measure these.
- **Path issues past the first ISP hop.** `tracert` sees the visible path
  but the "why" of a route change (peering dispute, undersea cable cut,
  BGP configuration) is not observable from a single host.
- **Wireless mesh handoff issues.** When your device roams between APs on
  the same SSID, ENACT sees the SSID stays constant but the BSSID/channel
  metadata may reveal it. Detecting *poor* roaming behavior specifically
  is out of scope.
- **DNS-over-HTTPS or DNS-over-TLS resolvers.** ENACT's DNS collector
  uses the standard `socket.getaddrinfo`, which follows the system resolver.
  If your system uses DoH/DoT, that's what ENACT measures. It cannot
  independently probe a specific DoH endpoint.

**ENACT may misreport:**

- **Latency during a Windows sleep/wake cycle.** Ping RTT may show
  suspiciously low values for the first cycle after wake, because the OS
  timing is briefly unusual.
- **Route fingerprints during ISP failover.** During failover, `tracert`
  may return a partial path with `*` timeout hops that hash differently
  than a full path, producing a route change event that is technically
  correct but represents transient probing conditions.

---

## 8. Operator's playbook

Quick reference for interpreting the dashboard at a glance.

**Dashboard says INTERNET: DEGRADED, DNS ok**
- ICMP is filtered somewhere between you and the reachability targets.
- Most common cause: VPN provider blocks ping; corporate firewall blocks
  outbound ICMP.
- Not an emergency in itself. If browsing works, this is a measurement
  limitation, not a network failure.

**Dashboard says INTERNET: OFFLINE**
- Both DNS and ping fail. This is a genuine reachability problem.
- Check: is Wi-Fi connected? Is the ISP up? Is a VPN connected but with a
  broken tunnel?

**A critical DNS outage fires**
- 90%+ of DNS lookups are failing over the last ~40 minutes.
- Probable causes in order: resolver misconfigured, ISP DNS server down,
  DNS-over-HTTPS misroute, security appliance blocking outbound port 53.
- Check the concurrent samples in the incident window, if connectivity
  is also failing, it's a broader outage, not a DNS-specific problem.

**A latency spike warning fires**
- One sample is 3x the recent median and above 100ms.
- Usually transient. Check if it self-recovers in the next cycle.
- If it repeats within an hour, mentally correlate with route changes
  and Wi-Fi state.

**A Wi-Fi degradation warning fires**
- RSSI dropped 15+ dB and is now below -70 dBm.
- Probable causes: you moved further from the AP, someone microwaved
  something nearby, a neighboring AP started broadcasting on the same
  channel.
- If DNS and latency stay clean, the wireless link is coping. If they
  degrade too, the degradation has crossed into operational impact.

**A route change info event fires**
- Someone routed something. It's normal.
- Only worth attention if you see the same target flip fingerprints
  repeatedly (flapping).

---

## 9. Revision history

- **v1.0 (2026)** : Initial specification. Defines the five collectors,
  four analyzers, three severity tiers, and the operator's playbook.

Future revisions should preserve the "honesty check" pattern for any new
analyzer added.
