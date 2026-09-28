# Brightline MVP product requirements

These requirements describe the product, not the current developer's uploaded
files. They must be considered when changing the application or reviewing a PR.

## Empty by default; local test data is not product content

- A fresh installation has no classes, students, uploaded files, report templates,
  completed samples, comments, scores, or generated reports. A Week 1 upload option
  is a UI convenience, not a seeded student record or uploaded reporting period.
- Users supply their own data. Developer and coworker uploads are local test
  material only. Never initialize an installation from that material.
- Keep the reporting capabilities in the application; do not remove them when
  clearing data or preparing a release. Do not hardcode current student identities,
  file paths, class names, or stored database IDs into production behavior.
- Version code, documentation, and clearly synthetic tests. Do not version local
  databases, uploaded files, generated student reports, credentials, tokens, or
  machine-specific settings. Preserve existing local uploads during Git operations.

## Two distinct report inputs for each class

- The original blank template is authoritative for layout, structure, section
  placement, design, and page artwork. A completed sample must not override it.
- Completed samples explain what kind of content belongs in each section. Analyze
  section meanings, text location, fonts and sizes, graph type/location where
  supported by evidence, and variation across samples/pages for that class.
- Preserve the individual teacher's wording. Organize source passages into the
  appropriate sections without rewriting, inventing, losing, or duplicating them.
  Never copy a sample student's feedback, identity, or results into another report.
- Evaluate each class independently. An ESS example is not a universal template
  or routing convention. Different subjects can have different section semantics,
  fonts, layouts, graphs, and feedback styles.
- High-precision handling across different formats is a continuing quality goal.
  Current local, evidence-based rules are not general semantic understanding or
  OCR. Unclear mappings and unsupported layouts need review, not silent guessing.
  Validate new formats separately before claiming equivalent quality.
- Build class-specific section profiles from completed samples: recognized heading,
  corresponding blank-template region, content features, typical text length and
  fill, rendered body font and size, and graph evidence. These profiles are local
  statistical evidence, not reusable sample prose.
- Split incoming feedback at paragraph and numbered-question boundaries. Record one
  destination, confidence score, and matching signals for every passage. Show those
  decisions in review. Low-confidence routing blocks report generation.

## Fitting and page rules

- Weekly Test feedback stays inside its original text area; shrink the body font
  as necessary instead of creating a continuation page for it.
- Additional teacher-comment pages are allowed only when the last page's comment
  area is at least half occupied. Build them from an appropriate full-width feedback
  page in the blank template, relabel the continuing section, and compact sparse
  remainders into earlier pages. Never use a completed sample as page artwork.
- Start from the completed sample's observed body size and adjust toward its typical
  fill. Smaller-than-8-point text is approved. The implementation uses a 7-point
  safety floor; if the page rules still cannot be met, request review rather than clip or
  silently discard text. Preserve readable fonts and inspect rendered output.
- Remove unused trailing blank-template pages from generated reports.
- Missing appropriate Weekly Test feedback uses this academy-requested assumption:
  the student missed the test, even if an imported score exists. Use the notice
  below, omit the conflicting test graph, and fit the report on one page:

  > 학생은 이번 위클리 테스트를 응시하지 않았기에 정확한 이해도 판단이 어렵습니다. 위클리 테스트를 나중에 한 번 풀어보시는 것을 추천드립니다.

  This is an explicit output policy, not verified attendance. Do not overwrite
  stored scores or source comments with the assumption. Assignment data and
  homework comments remain prerequisites.

## Connection and release checks

- Remember private local Google OAuth configuration and a valid saved refresh token
  across restarts. Reuse the token automatically until the user disconnects or Google
  revokes it. If refresh fails, show exactly `Google connection expired. Reconnect.`
  and require a new connection. Configuration and tokens remain outside version control.
- Before publishing changes, run the regression suite and the isolated empty-start
  test. Review the staged file list for uploads, secrets, and local configuration.
- Test layout changes using synthetic documents as well as authorized local
  examples; never commit the latter. Verify content conservation and pagination,
  and distinguish implemented behavior from future cross-format quality goals.
