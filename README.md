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
the Horux console) and the **resource id** the telemetry belongs to. The ingestor URL
defaults to `https://ingest.horux.io`.

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

Secrets the publish workflows need, as repository secrets:

| Secret | Used by | Without it |
|---|---|---|
| `NPM_PUBLISH_TOKEN` | TypeScript → npmjs | the publish step fails with a named error |
| `PYPI_PUBLISH_TOKEN` | Python → PyPI | same |
| — | Go | none; the proxy serves the tag |
| `GITHUB_TOKEN` | Java → GitHub Packages | built in |

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
