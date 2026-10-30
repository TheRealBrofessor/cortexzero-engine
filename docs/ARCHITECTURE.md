# CortexZero engine — architecture

**More capability. Same model.** A 1B model only *routes* a message to a branch id; everything else is
deterministic code and data written (and reviewed) by people.

```
message
  │
  ├─1 safety rules (regex, raw text, every pack)  ── match ─→ fixed response (988 / 911 / scam)   ⚠
  │
  ├─2 consent reply? ("yes" to a pending action)   ── yes ──→ run the previewed action + verify     ✓
  │
  ├─3 normalize: protect paths/IPs/URLs, fix typos only toward known words, keep the original
  │
  ├─4 index (type-ahead, no model)
  │     synonyms fold → inflection stems → IDF-weighted pattern match (+bigrams, +coverage tie-break)
  │     core gate: a branch is a candidate only if its core concept words are in the message
  │     near-miss decoys: out-of-scope look-alikes; if one scores ≥ the leader → "not in library"   ∅
  │
  ├─5 router: clear winner → index decides · nothing plausible → none
  │           ambiguous middle → 1B model picks among the top 3 | none | ask  (JSON-schema enum)
  │
  ├─6 handler (by branch kind)
  │     answer : fixed text + cited sources (+ optional online excerpt, OFF by default, cached, dated)
  │     check  : read-only argv steps (no shell) → rules pick the message from the outputs        ✓
  │     action : preview argv + risk → explicit consent → run → verify step                      ✓/⚠
  │
  └─7 evidence log (JSONL): question, cleaned text, corrections, route + candidates, steps run, answer
```

The model can never produce a command, an answer text, or a branch id that was not offered.
Commands are fixed argv lists in pack YAML, run by `LocalRunner` (no shell, stdin closed, minimal env).

## Packs (downloadable YAML libraries)
```
packs/<name>/
  pack.yaml           name, version, domain, status (draft|unverified|reviewed), safety_critical,
                      sources, synonyms, safety rules
  branches/*.yaml     one branch each: id, title, kind, patterns, core, text|steps+rules, sources, online
  near-misses.yaml    topic → out-of-scope look-alike phrasings
  cases/dev.yaml      the pack's own routing dev cases
```
Validation on load rejects shell strings, non-bare programs, unknown slots/transforms, actions without
risk, reviewed packs without a reviewer, and safety-critical answers without sources. `cz pack validate`
adds routing lint (dead core words, duplicate patterns, near-misses that collide with real patterns).
`cz compile` writes one JSON (packs + index tables, keyed by source hashes) for fast startup; a stale
file is ignored automatically.

Offline-first: every answer is complete from the pack alone. `--online` only appends a cited, dated
excerpt from the branch's `online:` https URL, cached for 7 days; failures fall back to the pack text.

## Code map
| file | job |
|---|---|
| `czengine/engine.py` | pipeline + evidence log |
| `czengine/normalize.py` | protected spans, typo fixing |
| `czengine/index.py` | synonyms, stems, IDF scoring, core gate, near-misses, type-ahead |
| `czengine/router.py` | thresholds; when the 1B model is consulted |
| `czengine/model.py` | Ollama / OpenAI-compatible 1B client, enum-constrained JSON |
| `czengine/handlers.py` | answer / check / action, slots, consent, verify |
| `czengine/runners.py` | `LocalRunner` (argv only, never a shell) |
| `czengine/library.py` | pack + branch schema and validation |
| `czengine/compiled.py` | pack compiler |
| `czengine/packtools.py` | `cz pack new|validate|list|tree` |
| `czengine/online.py` | optional cited online sources |
| `czengine/policy.py` | argv allowlist for check / action steps (load time + runner) |
| `czengine/tui.py` | live type-ahead prompt |

## Branch tree (generated: `cz pack tree`)
```
home-network v0.2.0 [draft] — Home and small-office network help for a Linux computer (NetworkManager, systemd).
├─ answer
│  ├─ net.bandwidth-hog: What is using my bandwidth
│  ├─ net.dns-change: Change DNS servers
│  ├─ net.guest-wifi: Set up guest Wi-Fi
│  ├─ net.port-forward: Set up port forwarding
│  ├─ net.router-restart: Restart the router properly
│  ├─ net.secure-network: Secure my home network
│  └─ net.wifi-password-change: Change the Wi-Fi password
├─ check
│  ├─ net.cert-check: Is a site's certificate valid
│  ├─ net.devices: Devices on my network
│  ├─ net.dns-which: Which DNS server am I using
│  ├─ net.ethernet: Wired (Ethernet) link check
│  ├─ net.firewall-status: Is the firewall on
│  ├─ net.mac: My MAC address
│  ├─ net.my-ip: What is my IP address
│  ├─ net.no-internet: No internet or nothing loads
│  ├─ net.packet-loss: Packet loss and lag check
│  ├─ net.port-listening: Is a port open on this computer
│  ├─ net.printer: Find my printer
│  ├─ net.public-ip: What is my public IP
│  ├─ net.router-address: Router address
│  ├─ net.site-check: Is a website down or just me
│  ├─ net.speed: Internet speed test
│  ├─ net.traceroute: Where the connection slows down
│  ├─ net.vpn-status: Is my VPN connected
│  ├─ net.wifi-networks: Nearby Wi-Fi networks and channels
│  └─ net.wifi-signal: Wi-Fi signal and band
├─ action
│  ├─ net.dns-flush: Flush the DNS cache (risk 1)
│  ├─ net.renew-ip: Renew my IP address (risk 1)
│  └─ net.wifi-reconnect: Reconnect Wi-Fi (risk 1)
└─ near-miss topics (→ not in library): woodworking-router, ship-and-travel-port, online-account-password, phone-and-tablet-wifi, printer-hardware, bluetooth-and-wearables, casting-and-streaming-devices, smart-home, buying-and-installing-hardware, isp-billing-and-plan, gaming-accounts-and-cheats, console-and-device-wifi, quality-of-service, public-wifi-captive-portal, tv-and-radio-channels, general-off-topic, other-device-settings
safety v0.1.0 [draft] — Always checked first. Fixed responses; never routed, never model-dependent.
├─ safety rules (checked first): crisis, emergency, scam
```
