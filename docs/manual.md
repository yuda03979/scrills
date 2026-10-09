# The manual

This is `scrills/SKILL.md` in the repository, unmodified — the file an agent reads when it picks
up the skill, and the fullest description of the surface. Point your harness at it: Claude Code
takes a symlink at `~/.claude/skills/scrills`, Pi at `~/.pi/agent/skills/scrills` (or under
`$PI_CODING_AGENT_DIR`), and anything else can take the file pasted into the session.

<!-- the :9 below skips SKILL.md's frontmatter exactly; tests/test_scrills.py::test_docs_include_offsets
     fails loudly if the frontmatter and this offset ever drift apart -->
--8<-- "scrills/SKILL.md:9"
