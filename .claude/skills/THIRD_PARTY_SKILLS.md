# Third-party agent skills

Skills vendored into this repository after a manual source review (no bundled script was executed during review).
Each skill keeps its upstream license file. Local modifications are listed per skill.

| Skill | Upstream | Commit | License | Review verdict | Local modifications |
|---|---|---|---|---|---|
| `cheminformatics` | https://github.com/awslabs/hcls-agent-skills/tree/main/skills/cheminformatics | `4603541` (2026-10-02) | MIT-0 | APPROVE: markdown only, no scripts, no network | none |
| `rdkit` | https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/rdkit | `a25cf9e` (2026-11-02) | MIT (repo `LICENSE.md`; skill frontmatter says BSD-3-Clause) | CAUTION → accepted with edits: scripts use RDKit and local file I/O only, no network | Removed the "Citing Scientific Agent Skills" section (instructed adding a citation to deliverables and fetching arXiv). Replaced the install section pinning `rdkit==2026.3.6` with "use the version pinned in `backend/`". |
| `webapp-testing` | https://github.com/anthropics/skills/tree/main/skills/webapp-testing | `ca45fc4` (2026-10-28) | Apache-2.0 | APPROVE: `scripts/with_server.py` starts the user-given server commands with `shell=True` and polls localhost ports; nothing else | none |

Project-specific knowledge (Ketcher API, tool registry, OpenAI SDK streaming) lives in the self-authored `chemomni-dev` skill.

Reviewed and not vendored: `trailofbits/skills` `modern-python` (CC-BY-SA-4.0; opinionated defaults), `orchestkit` `llm-streaming` (no longer present upstream; nearest rule is Next.js-only).
