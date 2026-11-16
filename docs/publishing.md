# SDK Publishing Guide

This guide covers publishing the Horux client SDKs to their respective package registries.

> **Read this first — three things changed when the SDKs moved out of the platform
> monorepo (16 Nov 2026), and older instructions below may still reflect the old setup.**
>
> 1. **Registries and credentials.** TypeScript publishes to **npmjs** via **trusted
>    publishing (OIDC)** — no npm token, provenance automatic, and the trusted publisher on
>    npmjs must name `release.yml` because npm validates the *calling* workflow. Python
>    publishes to **PyPI** by trusted publishing as well (no token at all — PyPI's pending
>    publisher creates the project on first use), not GitHub The Python workflow previously uploaded to `nuget.pkg.github.com` — the
>    NuGet endpoint — behind `continue-on-error`, so it had never published anything.
>    Java is still on GitHub Packages and is the one SDK that needs authentication to
>    consume; moving it to Maven Central is an open task.
> 2. **Tag formats.** The Go module lives in `go/`, so its tags must be `go/vX.Y.Z` —
>    that is how the module proxy finds a module in a subdirectory. The rest are
>    `<language>-vX.Y.Z`.
> 3. **You do not push tags by hand.** Run `3 · Release — SDKs (manual)`; it calls the
>    publish workflow, which publishes and then creates the GitHub Release, and the
>    Release creates the tag. The manual `git tag && git push` recipes below are the old
>    flow, kept because they document what the automation does.
>
> The table in [the root README](../README.md) is authoritative for registries and secrets.

## Overview

Horux provides **four** client SDKs. All four implement the same ingestion
backpressure contract (`the platform's ingestion-backpressure contract (Aterve/Horux, docs/architecture/ingestion-backpressure.md)`), and all
four are published automatically via GitHub Actions workflows.

| SDK | Source | Package | Registry | Workflow | Tag |
|---|---|---|---|---|---|
| TypeScript/JavaScript | `typescript` | `@aterve/horux-client` | **npmjs** | `publish-typescript.yml` | `typescript-v1.2.3` |
| Go | `go` | `github.com/Aterve/horux-sdk/go` | Go module proxy (Git tags) | `publish-go.yml` | **`go/v1.2.3`** |
| Python | `python` | `horux-client` | **PyPI** | `publish-python.yml` | `python-v1.2.3` |
| Java | `java` | `io.horux:horux-client` | GitHub Packages (Maven) | `publish-java.yml` | `java-v1.2.3` |

### One version across all four

The four SDKs ship as **one version number**. They are deliberate ports of one
another - the same bounded queue, the same five-way failure classification, the
same 16-batch cycle cap - so a user comparing two of them must be able to read
one version and know which behaviour they have.

⚠️ **Amended 16 Nov 2026: that rule governs BEHAVIOUR, not every patch.** The SDKs now
live in per-language directories with their own tags and registries, and the first thing
that happened in practice was a TypeScript-only patch for a README change -- which moves no
behaviour in any client and so cannot create the drift this rule exists to prevent. The rule
now reads: **a behavioural change bumps all four together; a docs- or packaging-only patch
may move one SDK alone.** If you are changing the bounded queue, the failure
classification, the cycle cap or the wire format, bump all four in one commit and release
them together, exactly as below.

Current versions -- no longer necessarily equal:

| SDK | Version | Published? |
|---|---|---|
| TypeScript | 1.1.1 | yes, npmjs |
| Go | 1.1.0 | not yet |
| Python | 1.1.0 | not yet |
| Java | 1.1.0 | not yet |

The places each version is recorded:

| SDK | Where the version lives |
|---|---|
| TypeScript | `typescript/package.json` (and `package-lock.json`) |
| Go | nowhere in the source - the Git tag `go/vX.Y.Z` *is* the version |
| Python | `pyproject.toml`, `setup.py`, `horux_client/__init__.py` (`__version__`) |
| Java | `pom.xml` |

Bump all of them in one commit, then push all four tags. A release that moves
one SDK and not the others is how the SDKs drift apart, and drift in a client
library is invisible until someone is debugging production.

### Breaking changes in 1.1.0

**The stats types no longer carry a `writer` block.** The ingestor's
`GET /api/v1/metrics/stats` and `/api/v1/logs/stats` used to return writer
counters - total written, batches written, write errors - that span *every*
tenant, to any tenant bearer token. That cross-tenant leak is closed: the
`writer` block is now returned only to the internal service key, and the
`buffer` block is unchanged apart from new `dropped_*` counters.

The SDK types drop the writer block rather than keeping it and documenting it
as empty. A counter that reads as fact and is not is worse than no counter, and
zero is indistinguishable from a genuinely idle ingestor. It was also not a
cosmetic concern: the TypeScript and Python clients dereferenced `writer`
unconditionally, so against the current server `stats()` raised a `TypeError` /
`KeyError` rather than returning anything at all. The new `dropped_*` counters
are surfaced in their place, which is the figure a client can actually act on -
data the server-side buffer took and then shed, loss no client-side retry can
recover.

Callers referencing `MetricsWriterStats`, `LogsWriterStats`, `WriterStats`,
`LogWriterStats`, `stats.writer` or `getWriter()` must drop those references.

A strict reading of SemVer wants a major bump for removing a public type. It is
released as a minor because Go's major-version module path (`.../v2`) would
change the import path that `services/data-ingestor/go.mod` pins by `replace`,
and because the removed fields could never hold a true value for an SDK caller.
Raise it with the owner if the four SDKs have external consumers.

---

## TypeScript SDK Publishing

### Package Details

- **Package Name**: `@aterve/horux-client`
- **Registry**: GitHub Packages (https://npm.pkg.github.com)
- **Workflow**: `.github/workflows/publish-typescript.yml`

The name in `package.json` is the name it is published under: `@aterve/horux-client`,
published to npmjs with `--access public`.

The manifest once declared `@horus/client` (note the missing `x`) and the workflow
rewrote it with `sed` at publish time, so the README, the manifest and the registry all
named the package differently - and fixing the typo in the manifest would have silently
turned the `sed` into a no-op. The manifest was corrected long ago, which means the `sed`
had been doing nothing for some time; it was **deleted in the move (16 Nov 2026)**. The
name lives in one place now.

### Prerequisites

**No external secrets required!** The workflow uses the built-in `GITHUB_TOKEN` for authentication.

**Requirements**:
- Repository must have Packages enabled
- Workflow needs `packages: write` permission (already configured)

### Publishing Process

#### Method 1: Tag-Based Release (Recommended)

Create a git tag to trigger automatic publishing:

```bash
# Navigate to TypeScript SDK directory
cd typescript

# Ensure version in package.json is correct
# Edit package.json if needed

# Commit any changes
git add package.json
git commit -m "chore: bump version to 1.2.3"

# Create and push tag
git tag sdk-ts-v1.2.3
git push origin sdk-ts-v1.2.3

# Or use alternative tag format
git tag ts-sdk-v1.2.3
git push origin ts-sdk-v1.2.3
```

The workflow will:
1. ✅ Extract version from tag (`sdk-ts-v1.2.3` → `1.2.3`)
2. ✅ Update `package.json` version
3. ✅ Run type checking
4. ✅ Build the SDK
5. ✅ Run tests
6. ✅ Publish to GitHub Packages
7. ✅ Create GitHub Release

#### Method 2: Manual Release (Workflow Dispatch)

Trigger a manual release from GitHub Actions:

1. Go to GitHub repository → Actions tab
2. Select "TypeScript SDK - Publish to GitHub Packages" workflow
3. Click "Run workflow"
4. Fill in the form:
   - **Version**: e.g., `1.2.3` (with or without 'v')
   - **Pre-release**: Check if alpha/beta/rc version
5. Click "Run workflow"

This is useful for:
- Publishing pre-release versions
- Re-publishing a specific version
- Publishing without creating a git tag first

#### Method 3: Unified Release Workflow

Use the unified release workflow to publish multiple components:

1. Go to GitHub repository → Actions tab
2. Select "Release - Unified SDK & Service Release" workflow
3. Click "Run workflow"
4. Fill in:
   - **Component**: `typescript-sdk`
   - **Version**: `1.2.3`
   - **Pre-release**: Check if applicable
5. Click "Run workflow"

### Version Management

**Semantic Versioning** (SemVer):
- **Major** (1.x.x): Breaking changes
- **Minor** (x.1.x): New features, backward compatible
- **Patch** (x.x.1): Bug fixes, backward compatible

**Pre-release versions**:
- Alpha: `1.2.3-alpha.1`
- Beta: `1.2.3-beta.1`
- Release Candidate: `1.2.3-rc.1`

**Pre-release versions**:
When publishing pre-release versions (alpha, beta, rc), the GitHub Release will be marked as "pre-release"

### Verification

After publishing, verify the package:

```bash
# Configure .npmrc to use GitHub Packages
echo "@aterve:registry=https://npm.pkg.github.com" >> .npmrc

# Authenticate with GitHub
npm login --scope=@aterve --registry=https://npm.pkg.github.com
# Username: your-github-username
# Password: your-github-token (with read:packages scope)

# Install and test
npm install @aterve/horux-client@1.2.3
```

Visit package page: https://github.com/aterve/horux/packages

---

## Go SDK Publishing

### Package Details

- **Module**: `github.com/Aterve/horux-sdk/go`
- **Registry**: Go module proxy (proxy.golang.org)
- **Documentation**: pkg.go.dev
- **Workflow**: `.github/workflows/publish-go.yml`

### Prerequisites

**No external secrets required!** Go modules are published via Git tags.

**Requirements**:
- Repository must be public or accessible to Go module proxy
- Tags must follow Go module versioning conventions

### Publishing Process

#### Method 1: Tag-Based Release (Recommended)

Create a git tag to trigger automatic publishing:

```bash
# Navigate to Go SDK directory
cd go

# Ensure go.mod is correct
cat go.mod

# Commit any changes
git add .
git commit -m "chore: prepare v1.2.3 release"

# Create and push tag (Go module format)
git tag go/v1.2.3
git push origin go/v1.2.3

# Or use alternative tag formats
git tag sdk-go-v1.2.3
git push origin sdk-go-v1.2.3

git tag go-sdk-v1.2.3
git push origin go-sdk-v1.2.3
```

The workflow will:
1. ✅ Validate the SDK (tests, build, formatting)
2. ✅ Extract version from tag
3. ✅ Create GitHub Release with:
   - Release notes
   - Changelog
   - Installation instructions
4. ✅ Build source archives for multiple platforms
5. ✅ Upload archives to GitHub Release
6. ✅ Notify Go proxy to fetch the module

#### Method 2: Manual Release (Workflow Dispatch)

Trigger a manual release from GitHub Actions:

1. Go to GitHub repository → Actions tab
2. Select "Go SDK - Create Release" workflow
3. Click "Run workflow"
4. Fill in:
   - **Version**: `v1.2.3` (must include 'v')
   - **Mark as pre-release**: Check if alpha/beta/rc
5. Click "Run workflow"

#### Method 3: Unified Release Workflow

Use the unified release workflow:

1. Go to GitHub repository → Actions tab
2. Select "Release - Unified SDK & Service Release" workflow
3. Click "Run workflow"
4. Fill in:
   - **Component**: `go-sdk`
   - **Version**: `v1.2.3`
   - **Pre-release**: Check if applicable
5. Click "Run workflow"

### Go Module Versioning

**Tag Format**: `go/vX.Y.Z`

This format is required because:
- The Go module is in a subdirectory (`go/`)
- Go module proxy expects tags in this format for submodules

**Version Rules**:
- Must start with `v` (e.g., `v1.2.3`)
- Follow semantic versioning
- Major version 2+ requires module path update (`/v2`, `/v3`, etc.)

**Example Tags**:
```bash
# Version 1.0.0 (initial release)
go/v1.0.0

# Version 1.1.0 (new features)
go/v1.1.0

# Version 1.1.1 (bug fix)
go/v1.1.1

# Version 2.0.0 (breaking changes)
# Requires updating go.mod to: module github.com/Aterve/horux-sdk/go/v2
go/v2.0.0
```

### Verification

After publishing, verify the module:

```bash
# Wait 2-3 minutes for Go proxy to index

# Check module is available
go list -m github.com/Aterve/horux-sdk/go@v1.2.3

# View all versions
go list -m -versions github.com/Aterve/horux-sdk/go

# Install and test
go get github.com/Aterve/horux-sdk/go@v1.2.3
```

Visit documentation: https://pkg.go.dev/github.com/Aterve/horux-sdk/go@v1.2.3

---

## Python SDK Publishing

### Package Details

- **Package Name**: `horux-client`
- **Registry**: GitHub Packages
- **Workflow**: `.github/workflows/publish-python.yml`
- **Build**: `python -m build` (sdist + wheel), checked with `twine check`

### Prerequisites

**No external secrets required** for GitHub Packages - the workflow uses the
built-in `GITHUB_TOKEN`.

Publishing to **PyPI** instead requires a repository secret:

- `PYPI_API_TOKEN` - a PyPI API token (scope it to this project, not the whole
  account)
- For a dry run, `TEST_PYPI_API_TOKEN` and `--repository-url
  https://test.pypi.org/legacy/`

### Publishing Process

#### Method 1: Tag-Based Release (Recommended)

```bash
cd python

# The version lives in three places and they must agree
#   pyproject.toml          version = "1.2.3"
#   setup.py                version="1.2.3"
#   horux_client/__init__.py  __version__ = "1.2.3"

git add pyproject.toml setup.py horux_client/__init__.py
git commit -m "chore(sdk-python): bump version to 1.2.3"

git tag sdk-python-v1.2.3
git push origin sdk-python-v1.2.3
```

The workflow extracts the version from the tag, runs the test suite, builds the
distributions, and publishes them.

#### Method 2: Manual Release (Workflow Dispatch)

Actions → "Python SDK - Publish" → Run workflow, with a version and an optional
pre-release flag.

### Local Build and Verification

```bash
cd python

python -m venv .venv && . .venv/bin/activate
pip install -e . build twine

# Tests (stdlib unittest - there is no pytest dependency)
python -m unittest discover -s tests

python -m build
twine check dist/*

# Install the built artifact into a clean environment and import it
pip install dist/horux_client-1.2.3-py3-none-any.whl
python -c "import horux_client; print(horux_client.__version__)"
```

### Known Issue

**FIXED in the move (16 Nov 2026).** `publish-python.yml` used to upload the wheel to
`https://nuget.pkg.github.com/<owner>` - the **NuGet** endpoint, which cannot accept a
Python distribution - under `continue-on-error: true`, and then cut a GitHub Release
regardless. So a "successful" Python release published nothing, silently, for as long as
the workflow existed.

It now uploads to PyPI through `pypa/gh-action-pypi-publish` using **trusted publishing
(OIDC)**: no API token at all, not even for the first upload, because PyPI's "pending
publisher" creates the project on first use. The `continue-on-error` is gone with it.

---

## Java SDK Publishing

### Package Details

- **Coordinates**: `io.horux:horux-client`
- **Registry**: GitHub Packages (Maven), per `pom.xml` `<distributionManagement>`
- **Workflow**: `.github/workflows/publish-java.yml`
- **Toolchain**: Java 11 target, Maven 3.9+, Jackson 2.16.x, JUnit 5

### Prerequisites

Publishing uses `GITHUB_TOKEN`; the workflow writes a `settings.xml` with a
`github` server matching the `<repository>` id in `pom.xml`.

Publishing to **Maven Central** instead additionally requires:

- `OSSRH_USERNAME` / `OSSRH_TOKEN` (Sonatype)
- `MAVEN_GPG_PRIVATE_KEY` / `MAVEN_GPG_PASSPHRASE` - Central requires signed
  artifacts, plus the `sources` and `javadoc` jars `pom.xml` already attaches

### Publishing Process

#### Method 1: Tag-Based Release (Recommended)

```bash
cd java

mvn versions:set -DnewVersion=1.2.3
mvn versions:commit

git add pom.xml
git commit -m "chore(sdk-java): bump version to 1.2.3"

git tag sdk-java-v1.2.3
git push origin sdk-java-v1.2.3
```

The workflow sets the version from the tag, runs `mvn test`, packages, and
`mvn deploy`s to GitHub Packages.

#### Method 2: Manual Release (Workflow Dispatch)

Actions → "Java SDK - Publish" → Run workflow.

### Local Build and Verification

```bash
cd java

mvn -B package          # compiles, runs the full JUnit suite, builds the jar
mvn -B -DskipTests package

# Consume it from a local project
mvn install
```

In `pom.xml` of the consuming project:

```xml
<dependency>
    <groupId>io.horux</groupId>
    <artifactId>horux-client</artifactId>
    <version>1.2.3</version>
</dependency>
```

And in `~/.m2/settings.xml`, to read from GitHub Packages:

```xml
<servers>
  <server>
    <id>github</id>
    <username>YOUR_GITHUB_USERNAME</username>
    <password>YOUR_GITHUB_TOKEN</password> <!-- read:packages scope -->
  </server>
</servers>
```

### Java coverage

`ci-java.yml` runs `mvn jacoco:report` and uploads `java/target/site/jacoco/jacoco.xml`.
`pom.xml` does declare the JaCoCo plugin, so this should produce a real report - but the
step is informational and has never been observed on a runner from this repository, so
treat the first green upload as the confirmation rather than this sentence.

---

## Publishing All Four SDKs Together

Use the unified release workflow to publish both SDKs with the same version:

```bash
# Option 1: GitHub Actions UI
# 1. Go to Actions → "Release - Unified SDK & Service Release"
# 2. Select component: "all-sdks"
# 3. Enter version: "1.2.3"
# 4. Click "Run workflow"

# Option 2: GitHub CLI
gh workflow run release.yml \
  -f component=all-sdks \
  -f version=1.2.3 \
  -f npm_tag=latest
```

This will:
- Publish the TypeScript SDK to GitHub Packages
- Create the Go SDK GitHub Release (the tag is the version)
- Publish the Python distributions
- Deploy the Java artifact
- Generate a unified summary

Use `component=all-sdks` rather than four separate releases: a shared version
number is only meaningful if the four are cut together.

---

## Release Checklist

### Before Release

- [ ] All four test suites pass **locally**, not just on CI - three of the four
      SDK workflows currently swallow test failures with `|| echo` plus
      `continue-on-error: true`, so a green workflow is not evidence
- [ ] Documentation updated (README, CHANGELOG)
- [ ] The same version bumped in **all four** SDKs (see "One version across all
      four" above)
- [ ] Breaking changes documented
- [ ] Migration guide written (if breaking changes)
- [ ] Examples updated
- [ ] Dependencies updated to latest stable versions

```bash
# The four suites, in full
(cd go     && go build ./... && go vet ./... && go test -race -count=1 ./...)
(cd typescript     && npm test)
(cd python && python -m unittest discover -s tests)
(cd java   && mvn -B package)
```

### TypeScript SDK Specific

- [ ] `npm run build` succeeds locally
- [ ] `npx tsc --noEmit` is clean
- [ ] `package.json` version is correct
- [ ] `package.json` has correct repository URL
- [ ] GitHub Packages is enabled for the repository
- [ ] Workflow has `packages: write` permission
- [ ] The `test` script's glob is still **unquoted**: the shell expands it into
      a file list, where a quoted glob needs Node 22+ to expand itself and
      fails on Node 20

### Go SDK Specific

- [ ] `go mod tidy` run and committed
- [ ] `go build ./...` succeeds
- [ ] `go test -race ./...` passes
- [ ] No `replace` directives in `go.mod`
- [ ] Tag format is correct (`go/vX.Y.Z`)
- [ ] Builds under the toolchain production actually uses: the ingestor's
      Dockerfile compiles this SDK with `golang:1.25-alpine` via a `replace`
      directive, while CI matrices only 1.21-1.23

### Python SDK Specific

- [ ] `pyproject.toml`, `setup.py` and `horux_client/__version__` all agree
- [ ] `python -m unittest discover -s tests` passes
- [ ] `python -m build` succeeds and `twine check dist/*` is clean
- [ ] The wheel imports in a clean virtualenv
- [ ] Tag format is correct (`sdk-python-vX.Y.Z`)

### Java SDK Specific

- [ ] `pom.xml` `<version>` bumped (via `mvn versions:set`)
- [ ] `mvn -B package` passes, including the wire-format tests - the one defect
      that discarded 100% of this SDK's traffic was a serialisation default
      that no test looked at
- [ ] `sources` and `javadoc` jars attach (they are bound in `pom.xml`)
- [ ] Tag format is correct (`sdk-java-vX.Y.Z`)

### After Release

- [ ] Verify package appears on registry (npm/pkg.go.dev)
- [ ] Test installation from registry
- [ ] GitHub Release created successfully
- [ ] Release notes are accurate
- [ ] Update documentation with new version
- [ ] Announce release (if significant)

---

## Troubleshooting

### TypeScript SDK Issues

**"npm publish" fails with 403 Forbidden**:
- Check repository has Packages enabled
- Verify workflow has `packages: write` permission
- Ensure `GITHUB_TOKEN` is being passed correctly
- Check package scope matches repository owner

**Package not showing in GitHub Packages**:
- Wait 1-2 minutes for GitHub to process
- Check GitHub Actions logs for errors
- Verify the workflow completed successfully
- Visit: https://github.com/OWNER/REPO/packages

**Cannot install package**:
- Ensure `.npmrc` is configured: `@aterve:registry=https://npm.pkg.github.com`
- Authenticate with: `npm login --scope=@aterve --registry=https://npm.pkg.github.com`
- Use a GitHub token with `read:packages` scope
- For private repos, token needs access to the repository

### Go SDK Issues

**"Tag already exists" error**:
- Delete tag locally: `git tag -d go/v1.2.3`
- Delete tag remotely: `git push --delete origin go/v1.2.3`
- Create new tag with correct version

**Module not appearing on pkg.go.dev**:
- Wait 10-15 minutes for indexing
- Manually trigger: Visit https://pkg.go.dev/github.com/Aterve/horux-sdk/go@v1.2.3
- Check tag format is correct
- Ensure repository is public

**"go get" fails to fetch module**:
- Verify tag exists: `git ls-remote --tags origin`
- Check tag format: Must be `go/vX.Y.Z`
- Ensure go.mod module path matches: `github.com/Aterve/horux-sdk/go`

**Workflow fails on validation**:
- Run locally: `cd go && go test ./...`
- Check for failing tests or build errors
- Ensure `gofmt` has been run
- Fix issues and push before creating tag

---

## Version Strategy

### Development Cycle

```
1.0.0 (stable)
  ↓
1.1.0-alpha.1 (feature development)
  ↓
1.1.0-alpha.2 (more features)
  ↓
1.1.0-beta.1 (feature complete, testing)
  ↓
1.1.0-rc.1 (release candidate)
  ↓
1.1.0 (stable)
```

### Publishing Pre-releases

**TypeScript SDK**:
```bash
# Alpha release
git tag sdk-ts-v1.1.0-alpha.1
# Workflow marks GitHub Release as "pre-release"
# Install: npm install @aterve/horux-client@1.1.0-alpha.1

# Beta release
git tag sdk-ts-v1.1.0-beta.1
# Workflow marks GitHub Release as "pre-release"
# Install: npm install @aterve/horux-client@1.1.0-beta.1
```

**Go SDK**:
```bash
# Pre-release version
git tag go/v1.1.0-alpha.1
# Install: go get github.com/Aterve/horux-sdk/go@v1.1.0-alpha.1

# Release candidate
git tag go/v1.1.0-rc.1
# Install: go get github.com/Aterve/horux-sdk/go@v1.1.0-rc.1
```

---

## Best Practices

### Versioning

1. **Follow SemVer strictly**:
   - Breaking changes = major bump
   - New features = minor bump
   - Bug fixes = patch bump

2. **Coordinate SDK versions** (optional):
   - Release both SDKs with same version for consistency
   - Users can easily identify compatible versions

3. **Use pre-release versions**:
   - Test major changes with alpha/beta before stable release
   - Get community feedback before 1.0.0

### Documentation

1. **Maintain CHANGELOG.md**:
   - Document all changes in each release
   - Follow "Keep a Changelog" format

2. **Write migration guides**:
   - For breaking changes, explain how to migrate
   - Provide code examples

3. **Update README**:
   - Keep installation instructions current
   - Update version badges

### Testing

1. **Automated testing**:
   - All tests must pass before release
   - Use CI to prevent broken releases

2. **Manual testing**:
   - Test installation from registry
   - Verify examples work with new version

3. **Backward compatibility**:
   - Avoid breaking changes in minor/patch releases
   - Deprecate features before removing

---

## Automation

### Automatic Version Bumping (Future)

Consider adding automatic version bumping based on commit messages:

```bash
# Install semantic-release
npm install --save-dev semantic-release

# Add to package.json
{
  "release": {
    "branches": ["main"],
    "plugins": [
      "@semantic-release/commit-analyzer",
      "@semantic-release/release-notes-generator",
      "@semantic-release/npm",
      "@semantic-release/github"
    ]
  }
}
```

### Conventional Commits

Use conventional commits for automatic versioning:

```bash
feat: add new metric type       # → Minor version bump
fix: resolve memory leak        # → Patch version bump
feat!: remove deprecated API    # → Major version bump (breaking)
```

---

## Support

For issues with SDK publishing:
1. Check this documentation
2. Review workflow logs in GitHub Actions
3. Open an issue on GitHub
4. Contact the SDK maintainers

---

## Quick Reference

### TypeScript SDK

```bash
# Tag and publish
git tag sdk-ts-v1.2.3
git push origin sdk-ts-v1.2.3

# Configure .npmrc
echo "@aterve:registry=https://npm.pkg.github.com" >> .npmrc

# Authenticate
npm login --scope=@aterve --registry=https://npm.pkg.github.com

# Install
npm install @aterve/horux-client@1.2.3
```

### Go SDK

```bash
# Tag and publish
git tag go/v1.2.3
git push origin go/v1.2.3

# Verify (wait 2-3 minutes)
go list -m github.com/Aterve/horux-sdk/go@v1.2.3

# Install
go get github.com/Aterve/horux-sdk/go@v1.2.3
```

### Python SDK

```bash
# Bump all three version sites, then tag
git tag sdk-python-v1.2.3
git push origin sdk-python-v1.2.3

# Build and check locally
python -m build && twine check dist/*

# Install
pip install horux-client==1.2.3
```

### Java SDK

```bash
# Bump and tag
mvn versions:set -DnewVersion=1.2.3 && mvn versions:commit
git tag sdk-java-v1.2.3
git push origin sdk-java-v1.2.3

# Build and test locally
mvn -B package
```

### Unified Release

```bash
gh workflow run release.yml \
  -f component=all-sdks \
  -f version=1.2.3 \
  -f prerelease=false
```
