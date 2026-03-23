# Feature Evaluation Prompt

Copy this into a new Claude Code session working in the ContextPulse project:

---

Read `docs/FEATURE_IDEAS.md` — it contains 10 feature ideas for ContextPulse Sight, prioritized by impact and effort.

For each feature:
1. **Technical feasibility** — Can we build this with the current architecture (Python, pystray, mss, SQLite, MCP server)? What dependencies would we need?
2. **Effort estimate** — T-shirt size (S/M/L/XL) with specific technical callouts
3. **Architecture impact** — Does this require schema changes, new MCP tools, config changes, or new processes?
4. **Risk** — What could go wrong? Platform-specific issues? Performance concerns?

Then recommend which 3 features to build first and outline a rough implementation plan for each.

Write your analysis back to `docs/FEATURE_FEASIBILITY.md`.
