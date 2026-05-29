---
name: security
description: Boundary + money-flow + OWASP + economic-exploit review.
reviewers:
  - ecc-security-reviewer
  - security-reviewer
  - silent-failure-hunter
---

# `security` mode

Two-pass framing: (1) input/output boundary (Discord, persistence, wiring,
token/secret handling); (2) money flow + auth inside services and cogs.
OWASP A01–A10 plus Friendex-specific economic exploits.

## Layer-slice usage

```
xan-multi-agent-review dir src/friendex/adapters/discord_bot/ --mode security
xan-multi-agent-review dir src/friendex/adapters/persistence/ --mode security
xan-multi-agent-review dir src/friendex/application/          --mode security
xan-multi-agent-review dir src/friendex/adapters/config.py    --mode security
```

## Adversaries to model

- Malicious guild member — game-economy exploits (self-trade, sandwich,
  sock-puppet activity botting)
- Malicious fund manager — rugpull paths around `withdraw` / `send_to_events`
- Guild admin — weaponising timeout/ban discipline penalty for short profit
- Discord-level abuse — markdown injection, embed character DoS, mention escape

See `.claude/xan-review-modes/README.md` for the exclusion-list step.
