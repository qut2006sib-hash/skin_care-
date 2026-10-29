# Source import provenance

The original archive's Git history is preserved in the original uploaded archive. Direct GitHub SSH networking was unavailable in this execution environment, so the connected GitHub API imported the five original branch snapshots in order. Each remote snapshot tree was checked equal to its original Git tree. API-created commit identities/dates differ; remote commit IDs therefore differ from archive commit IDs. No branch was force-pushed. Use a fresh clone of the remote for future development; do not push the old archive branches over the remote.

| Branch | Archive commit | Remote import commit |
|---|---|---|
| `main` | `0fa297963ef2e8b317e0de57eda5c5d1f17145e5` | `5f4a281cc22018731551d7befc58cbec8fee405c` |
| `feat/pr1-postgres-schema-and-seed` | `457f9a3aeb017fd0e0f7b70453f5f433556c5525` | `d49dfe929b70851796407a6c235c2ddd2343ad7e` |
| `feat/pr2-inventory-and-order-service` | `12bf6b9a41b4241179b6f504f99bb5f4e24b2990` | `0b912d0ba83b6a7855273e8a4235cb594ee1211f` |
| `feat/pr3-drf-api-and-nextjs-rtl-pwa` | `2dc8e766712ce1b8e55a83ee26686c4fa4f14d18` | `79e9e03880dbf0a03bd2c07be522d9b66650da0e` |
| `feat/pr4-concurrency-and-acceptance-tests` | `d308f19af2eec9c2615e3d9094dd96642228b2f1` | `dda967025430c18dd1e9b61a5630f7b2eee37c6d` |

New implementation lives on `feat/complete-local-mvp`, based on the imported fourth feature snapshot. `main` remains the original architecture documentation baseline. The original feature branches remain reviewable and are not merged into main.

