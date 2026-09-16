# secrets/

Real credentials live here and nothing in this folder is committed.

Expected:

* `hoshyarseo-service-account.json` — the Google service account key for the
  hoshyarseo project. Used by Search Console through
  `SEO_AGENT_GSC_CREDENTIAL_*`.

A credential that reaches a public repository is burned even if the commit is
reverted: the whole history is cloned by scanners within minutes. Rotate it
rather than deleting the commit.
