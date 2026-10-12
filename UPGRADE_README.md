# Compliance tracker verification & coverage upgrade

Upload the files in this package to the **root of the existing GitHub repository**, replacing files with the same names. New files can simply be added.

## What this upgrade adds

1. **Strict source verification** — unregistered domains can only become inactive `pending verification` source candidates. They cannot create verified findings automatically.
2. **Discovery vs verified findings** — search-result snippets are discovery evidence only. A finding is `verified` only after a primary-source page is retrieved and passes the evidence gate.
3. **Curated source registry** — `regulatory_source_registry.py` seeds legislation and tax/customs sources across the EU, all EU Member States, EEA countries, Switzerland and the UK.
4. **National tax/customs monitoring** — every jurisdiction is searched by separate product, environmental, tax and customs query groups against pre-approved domains.
5. **Expanded multilingual detection** — material-event and legal-reference extraction includes multilingual vocabulary and common national instrument terms.
6. **Evidence-bounded scope** — scope text no longer says a measure “applies” unless retrieved evidence explicitly identifies operator/obligation signals. Otherwise it states that scope is not established.
7. **Evidence-led summaries** — summaries report detected legal reference, development type and source sentences, while keeping unsupported dates/applicability unconfirmed.
8. **Rule-based business impact** — impact now reflects company applicability plus event severity instead of assigning every automatic finding `Informational`.
9. **Source-specific business actions** — actions are built from obligations/operators actually detected in the source evidence and always instruct primary-source verification.
10. **Broader regulatory developments** — official guidance, enforcement developments, consultations/proposals and other authority developments can be captured even without a conventional regulation number, but require direct primary-source evidence to become verified.
11. **Event correlation/deduplication** — findings share an `event_key`; repeated sources for the same event are correlated instead of blindly duplicating the event.
12. **Separate assurance scores** — `source_quality_score` and `evidence_quality_score` are stored independently, alongside the legacy combined confidence score.
13. **Automated-decision audit trail** — every stored candidate/verified decision writes its stage, reasons and evidence URL to `automated_decisions`.
14. **Company applicability filters** — edit `company_profile.json` to describe markets, economic roles, product/compliance categories and keywords. Findings receive `Likely relevant`, `Possibly relevant`, `Unclear`, or `Not currently relevant` plus reasons.

## Important verification model

A search-engine snippet **cannot** become a verified finding. It can only create a candidate. A verified finding requires:

- a pre-approved source in the source registry or a source manually approved in the UI;
- a directly retrieved primary-source page;
- a current-reporting-week publication/update date;
- a material regulatory event;
- adequate evidence quality;
- either a legal reference or a qualifying official regulatory/authority development.

This intentionally prioritizes precision and auditability over maximum recall.

## Configure company applicability

Edit `company_profile.json`. The supplied values are examples and must be replaced with the company's actual markets, roles and product/compliance scope. Leaving a list empty means “do not restrict by this dimension.”

`COMPANY_PROFILE_PATH` can optionally point to another JSON file.

## Source registry maintenance

`regulatory_source_registry.py` is now the trust boundary. Treat it as controlled compliance configuration:

- only mark a source `verified official source` after confirming the exact publisher/authority;
- use `likely official source` where an official role is established but operational review is still desired;
- newly discovered sources remain inactive and `pending verification` until approved;
- review the registry periodically because government portals and URLs can change.

## Database migration

No destructive migration is required. `db_repository.init_db()` adds the new columns/tables to an existing SQLite database. Existing findings without a `finding_status` are treated as legacy verified findings by the default view.

New finding fields include:

- `finding_status`
- `development_type`
- `event_key`
- `source_quality_score`
- `evidence_quality_score`
- `evidence_basis`
- `applicability_status`
- `applicability_score`
- `applicability_reasons`
- `decision_trace`
- `correlated_sources`

A new `automated_decisions` table stores the automated decision audit trail.

## UI behavior

The normal Overview, Developments, Timeline and exports use verified findings only. The Sources page includes a separate discovery-candidate table so weak/unverified findings cannot silently enter the normal compliance report.

## Tests

Run:

```bash
pytest -q
```

The supplied package passes the existing tests plus the new verification/control tests.
