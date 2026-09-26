---
name: screen-anticoagulant-notes
description: Screen a patient's clinical notes for anticoagulant therapy against a protocol's no-anticoagulant rule. Use when a screening run must settle the no_anticoag rule from notes.
allowed-tools: search_notes read_note evaluate_rule draft_packet submit_packet
metadata:
  book: "Agent Harness Engineering, Chapter 5"
---

# Screen notes for anticoagulants

1. Evaluate the numeric rules first; they need no notes.
2. Search once with the generic class and the common drugs, e.g. `anticoagulant`, `warfarin`, `apixaban`.
3. If that finds nothing, search once with brand names, e.g. `Eliquis`, `Xarelto`, `Coumadin`, and abbreviations such as `DOAC`.
4. Do not repeat a search whose terms appear in `pending.searched_terms`.
5. Read every unread hit, then evaluate `no_anticoag` again.
6. Draft the packet only when `pending.unfinished` is empty.
