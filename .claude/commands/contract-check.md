---
description: Verify backend responses, frontend consumers and mock fixtures still agree
---

Check that CONTRACTS.md, the backend, and the frontend mocks are still in sync.

1. Read `CONTRACTS.md`.
2. For each endpoint defined there, find the backend handler and compare the actual response
   shape: field names, types, enum values, nesting.
3. Compare each file in `frontend/src/mock/` against the same contract.
4. Check the frontend components that consume each shape for fields they read that the
   contract does not define.

Report any drift as a table: field, contract says, backend says, mock says.

Do not fix anything. Drift in CONTRACTS.md is a conversation between both developers, not a
unilateral edit. Report and stop.
