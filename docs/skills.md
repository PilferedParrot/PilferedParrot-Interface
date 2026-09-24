# Local skill metadata preview

Skill discovery is disabled by default. To try it, add explicit local folder
paths to `config.json`:

```json
"skills": {
  "enabled": true,
  "roots": ["~/my-skills"]
}
```

Open **Preferences → Local skills → Preview local skills** to scan. PPI checks
`SKILL.md` in each configured folder and one level of child folders. It reads
only bounded UTF-8 files with a YAML frontmatter block containing `name` and
`description`, then displays those two fields and a root-relative label. Hidden
folders and symbolic-link children are skipped. Instructions after the
frontmatter are not returned, interpreted, or added to a provider prompt.

Discovery runs only when requested from the Work dashboard. It does not select
or change a provider, model, permission mode, or prompt. Removing the setting,
setting `enabled` to `false`, or clearing `roots` disables scans.
