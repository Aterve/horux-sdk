# Horux SDKs

Official client libraries for [Horux](https://horux.io) — a multi-tenant platform for
service monitoring, alerting, log aggregation and metrics collection.

Each client pushes metrics, logs and traces to a Horux ingestor over HTTPS, with
batching and retry handled for you. They are thin on purpose: no framework, no agent, no
background daemon you have to operate.

| SDK | Package | Install |
|---|---|---|
| [Go](go/) | `github.com/Aterve/horux-sdk/go` | `go get github.com/Aterve/horux-sdk/go` |
| [TypeScript](typescript/) | [`@aterve/horux-client`](https://www.npmjs.com/package/@aterve/horux-client) | `npm install @aterve/horux-client` |
| [Python](python/) | [`horux-client`](https://pypi.org/project/horux-client/) | `pip install horux-client` |
| [Java](java/) | `io.horux:horux-client` | see [java/README.md](java/README.md) |

[![CI — Go](https://github.com/Aterve/horux-sdk/actions/workflows/ci-go.yml/badge.svg)](https://github.com/Aterve/horux-sdk/actions/workflows/ci-go.yml)
[![CI — TypeScript](https://github.com/Aterve/horux-sdk/actions/workflows/ci-typescript.yml/badge.svg)](https://github.com/Aterve/horux-sdk/actions/workflows/ci-typescript.yml)
[![CI — Python](https://github.com/Aterve/horux-sdk/actions/workflows/ci-python.yml/badge.svg)](https://github.com/Aterve/horux-sdk/actions/workflows/ci-python.yml)
[![CI — Java](https://github.com/Aterve/horux-sdk/actions/workflows/ci-java.yml/badge.svg)](https://github.com/Aterve/horux-sdk/actions/workflows/ci-java.yml)

## Quick start

Every client needs two things: an **API token** (`tenant_<tenant-id>_<secret>`, created in
the Horux console) and the **resource id** the telemetry belongs to. The ingestor URL is
configurable; every client has a sensible default.

```go
import client "github.com/Aterve/horux-sdk/go"

cfg := client.DefaultConfig()
cfg.APIToken = os.Getenv("HORUX_API_TOKEN")
cfg.ResourceID = "my-resource-uuid"
c, err := client.NewClient(cfg)
```

The per-language READMEs carry the full API, configuration and examples.

## Releasing

One workflow, **`3 · Release — SDKs (manual)`**: pick a language (or `all`) and a version.
It calls that language's publish workflow, which runs the gates, publishes to the registry
and creates the GitHub Release — and the Release is what creates the tag.

```
Actions → 3 · Release — SDKs (manual) → Run workflow
  component: python     version: 1.2.0     prerelease: false
```

Tag formats differ for one reason worth knowing: the Go module lives in `go/`, so its
version tags **must** be `go/vX.Y.Z` — that is how the Go module proxy finds a module in a
subdirectory. The others are `<language>-vX.Y.Z`.

[`docs/publishing.md`](docs/publishing.md) is the long form: per-language detail, version
policy, verification steps and the failure modes each workflow has actually hit.

### Credentials

| SDK | Registry | Credential |
|---|---|---|
| TypeScript | npmjs | **trusted publishing (OIDC)** — no secret once bootstrapped |
| Python | PyPI | **trusted publishing (OIDC)** — no secret, ever |
| Go | Go module proxy | none — the proxy serves the tag |
| Java | GitHub Packages | `GITHUB_TOKEN`, built in |

#### PyPI trusted publishing

Same mechanism, and simpler, because **PyPI supports a "pending publisher"**: you register
the publisher for a project that does not exist yet, and the first trusted publish creates
the project and converts the pending publisher into a normal one. So unlike npm there is no
token bootstrap at all — the Python SDK never needs a secret.

Register it at [pypi.org/manage/account/publishing](https://pypi.org/manage/account/publishing/):

| Field | Value |
|---|---|
| PyPI project name | `horux-client` |
| Owner | `Aterve` |
| Repository name | `horux-sdk` |
| Workflow name | `release.yml` |
| Environment | *(optional; PyPI recommends one with manual approvers)* |

A pending publisher does **not** reserve the name — if someone else registers `horux-client`
before the first publish, the pending publisher is invalidated.

The upload uses `pypa/gh-action-pypi-publish`, pinned by commit SHA, which performs the OIDC
exchange itself. `packages-dir` is `python/dist`, because an action's paths resolve against
the workspace and not against the job's `working-directory` — an easy and silent mistake.

⚠️ **PyPI's docs do not say which filename to configure when the publish job lives in a
reusable workflow.** PyPI matches the OIDC `workflow_ref` claim, and GitHub sets that to the
workflow that *started* the run, which is `release.yml` — so that is what the table above
says. Rather than leave it to inference, the workflow prints `github.workflow_ref` just
before uploading, so the first release shows you the exact value PyPI will compare against.
A mismatch is a clear 403 from PyPI naming the claims it received, not a silent no-op.

#### npm trusted publishing

The TypeScript publish exchanges the workflow's OIDC token for publish rights, so there is
no long-lived npm token to rotate or leak, and **provenance is attached automatically** for
a public package from a public repository.

Three things about it are easy to get wrong, and all three are encoded in the workflows:

1. **npm validates the *calling* workflow's filename.** `publish-typescript.yml` is invoked
   by `release.yml`, so the trusted publisher on npmjs must name **`release.yml`**. Configure
   `publish-typescript.yml` and every release is rejected at the registry.
2. **`id-token: write` is needed on both the parent and the child** job. `release.yml`'s
   `typescript` job and `publish-typescript.yml`'s `publish-npmjs` job both declare it, and
   `scripts/check-workflow-permissions.mjs` fails the build if they fall out of step — it
   caught exactly that omission when this was first wired.
3. **One entry point only.** `publish-typescript.yml` is `workflow_call`-only for this
   reason: a direct `typescript-v*` tag push or a dispatch of that file presents a different
   workflow identity and cannot publish.

It also needs npm ≥ 11.5.1 and Node ≥ 22.14. The workflow asserts the npm floor and upgrades
if the runner's Node ships something older, because a too-old npm does not report a missing
feature — it quietly asks for a token and then fails authentication.

**Bootstrapping, once:** npm will not let you configure a trusted publisher for a package
that does not exist, so the first publish of `@aterve/horux-client` needs a token. Set
`NPM_PUBLISH_TOKEN`, run one release, configure the trusted publisher
(Settings → Trusted Publisher → GitHub Actions, workflow `release.yml`), then **delete the
secret**. The publish step takes the OIDC path whenever that secret is absent and logs which
path it used, so there is no ambiguity about which credential published a given version.

⚠️ **Java is the one SDK that is not yet on a public registry.** It publishes to GitHub
Packages, which requires a reader to authenticate — wrong for an open client library.
Moving it to Maven Central needs an OSSRH account and GPG signing keys, which is a
deliberate next step rather than something to improvise.

## What gates a change

| Check | Go | TypeScript | Python | Java |
|---|---|---|---|---|
| format / lint | blocking | *no linter configured* | blocking | informational |
| type check | n/a (compiler) | blocking | blocking | n/a |
| tests | blocking | blocking | blocking | informational |
| build / package | blocking | blocking | blocking | blocking |
| dependency audit | informational | informational | informational | informational |

Two of those deserve their honesty: the TypeScript package has no linter at all (the CI
step that pretended to run one has been replaced by a guard that fails if a `lint` script
ever appears without this table being updated), and the Java checks could not be verified
on the machine this repository was assembled on — there was no Maven — so CI is their
first real run. Dependency audits are advisory everywhere by choice: a published CVE
should open an issue, not block a release.

The publish path runs the same gates again and **blocks on all of them**, including the
Python and Java tests that CI treats as informational. Shipping a failing build is the one
thing none of these workflows will do quietly.

## Contributing

Issues and pull requests are welcome. Each SDK is self-contained: work in its directory,
run its own CI commands (the per-language README lists them), and keep the four APIs
recognisably the same shape as each other.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
