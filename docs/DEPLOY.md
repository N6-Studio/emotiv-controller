# Deploy / releases (GitHub Actions)

## Bump version

1. Edit repo-root [`VERSION`](VERSION): one line, **no `v` prefix** — e.g. `0.1.0` or `0.2.0-beta.1` (semver + optional prerelease suffix per the tag workflow regex).
2. Merge to **`main`** or **`master`** (push must include that commit as `GITHUB_SHA` for the tag).

If `refs/tags/v{VERSION}` already exists on the remote, the tag job exits successfully and does nothing (tags are not moved).

## Workflows (order)

| Workflow | When | What |
|----------|------|------|
| [`ci.yml`](.github/workflows/ci.yml) | Push/PR to `main`/`master` | Linux tests (`scripts/ci-test.sh`). |
| [`tag-from-version.yml`](.github/workflows/tag-from-version.yml) | Push to `main`/`master` that changes `VERSION`, or manual **Run workflow** | Validates `VERSION`, skips if tag exists, else **annotated tag** `v{VERSION}` on the pushed commit (`GITHUB_TOKEN`), then `gh workflow run release-windows.yml --ref <tag>`. Needs `contents: write` + `actions: write`. |
| [`release-windows.yml`](.github/workflows/release-windows.yml) | Tag push matching `v*` **or** `workflow_dispatch` with ref = that tag | Windows build, GitHub Release with `app.exe` + `latest.json`. |

**Why two triggers on release:** Pushes done with `GITHUB_TOKEN` do not fire `on: push: tags` for downstream workflows, so automation dispatches the release run explicitly. A human `git push origin v1.2.3` still hits `push: tags` and runs release without dispatch.

## Note (partial failure)

If **Create and push tag** succeeds but **Start release workflow** fails, the tag exists on GitHub but no release job was started. Fix the failure, then either run **Release Windows EXE** manually with ref set to that tag (`v…`), or adjust tags/`VERSION` per your policy — **Tag from VERSION** will not re-tag the same version while the remote tag exists.
