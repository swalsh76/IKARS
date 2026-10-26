## What and why

<!-- Slice id, what it builds, link to its BUILD-LOG entry. -->

## Mini review (R14)

Per commit (each commit's diff was reviewed for):

- [ ] Correctness and edge cases
- [ ] Money stays fixed-point integers (dollars at 10^-6, counts at 10^-2)
- [ ] Reuse: no duplicate of existing code; shared code in the shared module
- [ ] Efficiency on hot paths
- [ ] Simplification
- [ ] Tests cover the change
- [ ] No secrets, keys, real host names, paths, Kalshi data or news content

Per merge (the branch diff against `main`, plus touched modules and their callers):

- [ ] Consistent across the slice
- [ ] Engine and terminal contracts still in step
- [ ] No drift from PLAN decisions
- [ ] Docs and guides current; BUILD-LOG entry has its **Review** section
- [ ] Each finding fixed here or parked in PLAN §9 with its reason
