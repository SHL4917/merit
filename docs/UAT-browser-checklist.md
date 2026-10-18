# Approval report browser UAT

Run before the demo using a draft with stated and inferred requirements, an open
issue, a provisional decision, a candidate, uncited source lines, and a prepared
baseline in a separate ready example. Record browser/version, date and reviewer.

- [ ] Generate with `merit report` and open `public/index.html` directly in a browser.
      Disconnect networking: all content, styles and controls still work.
- [ ] Read readiness first. Each blocked requirement is named with its issue or
      validation problem and a next action. An empty project explains how to start.
- [ ] Filter requirements by each status, then each provenance. Combine both and
      confirm only matching cards remain. Check the count and Clear filters.
- [ ] Use Tab and Enter/Space to expand a requirement. Review statement, separate
      status/provenance badges, acceptance criteria, quote, speaker, source,
      session date and line span. Repeat at narrow width and 200% zoom.
- [ ] Confirm Candidates says "not requirements", with rationale and author, and
      candidates are absent from the traceability matrix and baseline.
- [ ] Confirm the issue board shows owner, question, affected requirements and both
      competing quotes. A self-note decision reads `awaiting_confirmation` even
      though its recorded issue status is resolved.
- [ ] Confirm decisions appear oldest first, with record type and evidence. A
      provisional badge remains until an evidenced second-person confirmation.
- [ ] Inspect the requirement-by-source matrix and follow a requirement link.
- [ ] Inspect uncited lines before the secondary percentage. Confirm the text
      explains that citation does not establish comprehension or completeness.
- [ ] Open a ready report containing a prepared manifest. Follow the shared tag
      handoff order: prepare, commit, review, merge, tag. After merge, use CI's
      release link pinned to the exact merge commit and press Publish in GitHub.
      The report itself must perform no merge or tag operation.
- [ ] Review a quote containing `</script><img src=x onerror=alert(1)>` and HTML
      punctuation. It must appear literally with no image, alert or broken page.

Results: not yet run. Automated structural tests do not substitute for this pass.
