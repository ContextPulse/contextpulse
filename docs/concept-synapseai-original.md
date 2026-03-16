# SynapseAI — Concept Document v1

*Original date: 2026-03-15*

## The Problem

Most AI assistant setups look like this: you have a chat window, you ask it things, it answers, and every conversation starts from zero. Even with tool access (email, calendar, CRM, databases), each session is isolated. The agent doesn't know what you did yesterday, what another agent discovered last week, or what's overdue. You end up re-explaining context, re-researching customers, and manually tracking priorities across dozens of tabs.

SynapseAI solves this by adding three things most AI setups lack: shared memory between agents, a learning system that accumulates knowledge over time, and scheduled automation that works while you don't.

## Three-Tier Architecture

### Tier 1: Framework (same for everyone)

The infrastructure that makes everything else work. Install once.

**Shared Knowledge Layer.** A set of registry files (structured JSON and markdown) that all agents can read from and write to. This is how Agent A's work becomes visible to Agent B without you copy-pasting context between conversations. Registries include:

- Open actions (your to-do list, written by any agent that discovers work to be done)
- Completed actions (what's been done, used for evidence and deduplication)
- Customer/project context (cached research so agents don't re-query the same thing)
- Resolution caches (mappings between names, IDs, and systems)
- Activity log (what each agent did and when)
- Channel priorities (which email senders and chat channels matter most)
- Meeting metadata (recurring meetings with prep notes and links)

As this grows past a certain size, the flat files become expensive to load into every conversation. At that point, a lightweight database (SQLite) with an API layer lets agents query just the rows they need instead of loading the whole file. The flat files stay as backups and for tools that prefer file access.

**Session Learning Loop.** A skill attached to every agent that handles the start and end of each session:

- On start: read prior lessons, check for unfinished work, surface anything that needs attention
- During work: observe and log notable events to a session journal (append-only, JSONL format)
- On end: a deterministic script processes the journal and routes entries to the correct registries

The journal is the key design choice. Agents write observations. A script does the routing. This matters because agents are unreliable at maintaining complex data structures, but they're good at noticing things. The script handles deduplication, validation, and merging reliably.

**Ecosystem Maintenance.** Two roles keep the system healthy:

- An "Architect" agent that designs and optimizes agents, skills, and workflows
- A "Custodian" agent that handles mechanical maintenance: organizing files, trimming stale data, checking integrity

The architect makes design decisions. The custodian handles housekeeping. Neither should do the other's job.

### Tier 2: Configuration (generated for you)

A guided setup process that scans your environment and interviews you about your work:

- What tools do you have connected? (email, chat, CRM, databases, browser, etc.)
- What does your role focus on?
- What are your most important communication channels?
- What recurring processes do you follow? (weekly reports, activity logging, document writing, etc.)

Based on your answers, the setup generates personalized registry files (pre-populated with your channels, contacts, and meeting metadata) and recommends which agents to create.

### Tier 3: Agents (you choose)

Agent templates organized into tiers based on how broadly they apply:

**Framework agents (always recommended):**
- Ecosystem manager: builds and optimizes everything else
- Workspace maintenance: weekly housekeeping

**Universal agents (useful for almost anyone with the right tools connected):**
- Morning briefing: scans email, calendar, chat, and open actions. Produces a ranked priority list for the day.
- Meeting intelligence: pre-loads context before meetings, captures live notes, produces structured debriefs afterward

**Role-specific agents (matched to your processes):**
- CRM activity logger: reviews the day's interactions and logs them
- Document writer: produces formal documents with self-scoring and iteration
- Research/evidence gatherer: systematic multi-source data collection
- Data/SQL analyst: focused database queries
- Content production: presentations, spreadsheets, formatted output

Each template is a YAML file with a parameterized system prompt, required skills, tool assignments, model recommendation, and knowledge base seed files. During setup, placeholders get replaced with your actual paths, tool prefixes, and team context.

## Core Design Principles

### 1. Consolidate Aggressively

The system evolved from 15 separate agents down to 7 visible ones. The lesson: specialized agents sound good in theory, but in practice they fragment your context. You end up switching between agents, re-explaining background, and losing continuity.

The ideal is one primary "workhorse" agent per major domain of your work that can operate in different modes (research mode, writing mode, logging mode, analysis mode) using skills to provide the relevant knowledge for each mode. Separate agents only for genuinely different jobs that need different models, different tool sets, or that run on a schedule without human interaction.

**When to use separate agents:**
- The task runs on a schedule (morning briefing, weekly maintenance)
- The task needs a cheaper/faster model (file organization doesn't need the strongest model)
- The tool sets are completely disjoint (a PowerPoint agent doesn't need database access)

**When to use modes within one agent:**
- The tasks share the same customer/project context
- You frequently switch between them in a single session
- They need the same integrations (email, CRM, chat, database)

### 2. Skills Are the Reusable Layer

Domain knowledge lives in skills (portable instruction documents), not in agent prompts. An agent prompt says who you are and how you work. Skills say what you know.

This means:
- The same methodology skill can serve multiple agents or modes
- Updating a skill updates every agent that uses it
- Skills can be shared with other people or exported to other platforms
- Agent prompts stay short (2-10KB) while skills carry deep knowledge (10-60KB each)

The prompt itself follows a lean structure: who you are (2-3 sentences), where knowledge lives (pointers to skills and knowledge base files), how you work (high-level workflow), critical rules (top 5-10), and a routing guide (if the user asks about X, use skill Y).

### 3. Shared Memory Is the Multiplier

The shared knowledge layer is what turns a collection of agents into a system. Without it, each agent starts from scratch. With it:

- Your morning briefing agent knows what your strategy agent did yesterday
- Your CRM logger knows which customers you engaged with today
- Your maintenance agent knows which data is stale
- Your next session with any agent picks up where the last one left off

The data flows in one direction during a session (agent observes, journal captures, script routes to registries) and is available to all agents on their next session start.

### 4. Automate the Operating Rhythm

The highest-value automations are the ones that run without you thinking about them:

- **Morning briefing** (daily, before you start work): Scans all your communication channels, cross-references with open actions and customer context, produces a ranked priority list.
- **Activity logging** (daily or weekly, end of day): Reviews what you actually did across all channels and logs it to your CRM.
- **Workspace maintenance** (weekly): Organizes misplaced files, prunes stale registry data, checks ecosystem health.
- **Ecosystem intelligence** (weekly): Analyzes patterns across the whole system. Detects stale customer data, overdue actions, missed follow-ups, and attention gaps.

### 5. Right Model for the Right Job

- **Strongest model + large context window:** Complex judgment, multi-source synthesis, leadership-facing writing, long multi-phase sessions.
- **Mid-tier model:** Structured/mechanical tasks where rules constrain the output enough. CRM logging, file organization, form filling, browser automation.
- **Extended thinking** is deliberately avoided for long workflows because the per-response overhead (~30 seconds) accumulates across many turns.

### 6. The System Learns

Every session makes the ecosystem slightly smarter:

1. **Per-session:** Journal captures observations. Processing script routes them to registries.
2. **Per-agent:** Each agent accumulates a lessons-learned file. Recurring mistakes get documented.
3. **Cross-agent:** Global lessons apply to all agents. Weekly intelligence analysis watches for systemic patterns.
4. **Skill evolution:** When a lesson has been seen multiple times, it gets embedded into a skill update. The original lesson is archived.

## What the Ideal State Looks Like

**Morning:** Automated briefing synthesizes overnight email, calendar, chat activity, and open actions into a ranked priority list.

**During the day:** You work with your primary agent. It reads your resumption prompt, knows what you were working on, has access to shared context. Observations flow into the session journal.

**End of day:** Automated logger reviews customer interactions and logs them to CRM.

**End of week:** Maintenance agent organizes files, trims stale data. Intelligence agent analyzes patterns and writes a digest.

**Over months:** Lessons accumulate. Resolution caches fill up. Customer context stays current. The system handles edge cases that would have stumped it in week one.

## Platform Requirements

- AI chat application supporting: multiple agents, tool/function calling, system prompts, skills/instruction injection, scheduled workflows
- MCP server support or equivalent tool integration layer
- File system access for shared knowledge layer
- Access to multiple model tiers

## Roadmap

### Near-term
- Auto-close matching (work completed -> open actions marked done)
- Inline meeting action extraction
- Proactive meeting prep (afternoon workflow for tomorrow's meetings)

### Medium-term
- Agent-capable action queue (tagged items picked up proactively)
- Daily momentum detection (emerging problems surface same-morning)

### Longer-term
- Cross-agent lesson consolidation (automated monthly dedup + promotion)
- Action item topic graph (dependency tracking, workstream views)

## Key Design Decisions

**Why consolidate agents?** Context windows are finite. One agent with multiple modes shares conversation context naturally.

**Why a journal instead of direct memory writes?** Agents make mistakes with structured data. Append-only journal + deterministic script is reliable and replayable.

**Why automate the boring stuff?** CRM logging and morning briefings are high-frequency, tedious, and have real consequences when skipped.

**Why two maintenance roles?** Mechanical maintenance runs cheaply on a schedule (mid-tier model). Design decisions need judgment (strongest model, on-demand).

**Why flat files first?** Simple, debuggable, work with any tool. Upgrade to SQLite via dual-write when data grows.

**Why YAML templates?** Raw configs have hardcoded paths. Templates use placeholders resolved during setup — portable to anyone.
