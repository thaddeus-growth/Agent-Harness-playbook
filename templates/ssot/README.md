# ssot/ — single source of truth

- **Owner files** (`user-stories.tsv`, `glossary.tsv`, and any rules file): plain business meaning in the owner's language. Agents never edit them; they propose changes in the `.agent.tsv` sibling's `proposed` column and apply only an answer the owner gave.
- **Agent files** (`*.agent.tsv`): status, proof commands, code locations, proposals.
- **Registries** (`constants.tsv`, message codes, decision keys, fact keys): read by code, guarded by tests. A number lives only in its registry; everywhere else uses its name.
- **`index.tsv`** lists every file here with its owner, reader and test. Create it first, while it is still empty, with its lint test.
- Ids never change and are never reused; a retired row is marked, not deleted.
