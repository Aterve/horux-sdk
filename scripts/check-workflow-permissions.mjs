#!/usr/bin/env node
/**
 * G14 — a reusable-workflow call that cannot grant what its callee asks for.
 *
 * WHY THIS IS NOT ALREADY COVERED
 *
 * A called workflow's jobs may request GITHUB_TOKEN permissions only equal to or narrower
 * than the CALLING job's. Ask for more and GitHub rejects the entire run before any job
 * starts — `startup_failure`, with no annotation, no log, no failing step and no indication
 * which workflow or scope was at fault. Every other job in the run, including ones that have
 * nothing to do with the call, never executes.
 *
 * Nothing catches it. It is not a YAML error, so a parser is happy; `actionlint` passes
 * (measured, all 30 workflows clean while four calls were broken); and it needs two files to
 * see, which is the same blind spot G3, G11 and G12 exist for. Worse, it is introduced by a
 * change that looks purely defensive: adding `permissions: contents: read` at the top of a
 * workflow to apply least privilege. For a normal job a later `permissions:` block overrides
 * it; for a reusable-workflow call there is nothing to override it, and the callee's own
 * `contents: write` becomes an impossible request.
 *
 * That is exactly what happened here. All four SDK release paths in `release.yml` were dead
 * from the day the top-level `permissions: contents: read` was added — and because startup
 * validation is static, a release of ANY component failed, including `prober`, whose run
 * skips all four SDK jobs. Found 16 Nov 2026 by dispatching a release for the first time
 * since; it had never been exercised after the hardening pass.
 *
 * WHAT IT CHECKS
 *
 *   For every `uses: ./.github/workflows/<callee>` in the repository: the permissions the
 *   calling job grants (its own `permissions:`, else the calling workflow's) against every
 *   scope the callee can request (each callee job's `permissions:`, else the callee
 *   workflow's). none < read < write.
 *
 * Neither side declaring anything is reported as `unverifiable`, not as a pass: the token
 * then gets the repository's default, which is a settings page this script cannot read.
 *
 * Dependency-free and line-based on purpose, so it can run in `ci-guards.yml`, which has no
 * `npm ci` step — the same constraint G12 works under. Shapes it relies on are asserted;
 * it exits 2 rather than reporting a clean run it did not perform.
 *
 * USAGE
 *   node scripts/check-workflow-permissions.mjs [--json] [<extra workflow file>...]
 *
 * Exits 1 on an impossible grant, 2 if it cannot parse what it needs.
 */

import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { join, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const WORKFLOWS = join(REPO, '.github/workflows');
const RANK = { none: 0, read: 1, write: 2 };

/** Deliberate exceptions. A stale entry fails, the way G1, G9, G11, G12 and G13 do. */
const ACCEPTED = [
	// { caller: 'release.yml', job: 'release-go-sdk', scope: 'contents', reason: '...' },
];

const args = process.argv.slice(2);
const json = args.includes('--json');
const extra = args.filter((a) => !a.startsWith('--'));

function fail(msg) {
	console.error(`[check-workflow-permissions] cannot run: ${msg}`);
	process.exit(2);
}

const indent = (l) => l.length - l.trimStart().length;

/**
 * The three things this check needs out of a workflow: its workflow-level permissions, and
 * per job, that job's permissions and the local workflow it calls. Line-based, because
 * `ci-guards.yml` has no dependencies available.
 */
function parse(file, text) {
	const lines = text.split('\n').filter((l) => !/^\s*#/.test(l) && l.trim() !== '');
	const out = { file, workflow: null, jobs: new Map() };

	// A `permissions:` block, or the `read-all` / `write-all` / `{}` shorthands.
	const readPerms = (i, baseIndent) => {
		const inline = lines[i].slice(lines[i].indexOf(':') + 1).trim();
		if (inline === 'read-all') return [{ all: 'read' }, i];
		if (inline === 'write-all') return [{ all: 'write' }, i];
		if (inline === '{}') return [{}, i];
		if (inline !== '') fail(`${file}: unsupported inline permissions '${inline}'`);
		const perms = {};
		let j = i + 1;
		for (; j < lines.length && indent(lines[j]) > baseIndent; j++) {
			const m = /^\s*([a-z-]+):\s*(read|write|none)\s*(?:#.*)?$/.exec(lines[j]);
			if (!m) fail(`${file}: cannot read permission line '${lines[j].trim()}'`);
			perms[m[1]] = m[2];
		}
		return [perms, j - 1];
	};

	let inJobs = false;
	let job = null;
	for (let i = 0; i < lines.length; i++) {
		const line = lines[i];
		const ind = indent(line);

		if (ind === 0) {
			inJobs = /^jobs:/.test(line);
			job = null;
			if (/^permissions:/.test(line)) [out.workflow, i] = readPerms(i, 0);
			continue;
		}
		if (!inJobs) continue;

		if (ind === 2 && /^\s*[A-Za-z0-9_-]+:\s*$/.test(line)) {
			job = line.trim().replace(/:$/, '');
			out.jobs.set(job, { permissions: null, uses: null });
			continue;
		}
		if (!job) continue;

		if (ind === 4 && /^\s*permissions:/.test(line)) {
			const [p, end] = readPerms(i, 4);
			out.jobs.get(job).permissions = p;
			i = end;
			continue;
		}
		if (ind === 4) {
			const u = /^\s*uses:\s*(\.\/\.github\/workflows\/[\w.-]+)\s*$/.exec(line);
			if (u) out.jobs.get(job).uses = u[1];
		}
	}
	return out;
}

/** What a caller job actually grants: its own block, else the workflow's, else unknown. */
function granted(wf, job) {
	if (job.permissions) return { perms: job.permissions, from: 'job' };
	if (wf.workflow) return { perms: wf.workflow, from: 'workflow' };
	return { perms: null, from: 'repository default' };
}

/** Every scope the callee can ask for, and the job that asks for the strongest level. */
function required(callee) {
	const req = new Map();
	const consider = (perms, where) => {
		if (!perms) return;
		if (perms.all) {
			const cur = req.get('*');
			if (!cur || RANK[perms.all] > RANK[cur.level]) req.set('*', { level: perms.all, where });
			return;
		}
		for (const [scope, level] of Object.entries(perms)) {
			const cur = req.get(scope);
			if (!cur || RANK[level] > RANK[cur.level]) req.set(scope, { level, where });
		}
	};
	for (const [name, job] of callee.jobs) consider(job.permissions ?? callee.workflow, `job ${name}`);
	consider(callee.workflow, 'workflow level');
	return req;
}

const files = readdirSync(WORKFLOWS).filter((f) => f.endsWith('.yml'));
if (files.length === 0) fail('no workflows found');
const parsed = new Map();
for (const f of files) parsed.set(f, parse(f, readFileSync(join(WORKFLOWS, f), 'utf8')));
for (const f of extra) {
	if (!existsSync(f)) fail(`no such file: ${f}`);
	parsed.set(basename(f), parse(basename(f), readFileSync(f, 'utf8')));
}

const findings = [];
let calls = 0;
for (const [file, wf] of parsed) {
	for (const [name, job] of wf.jobs) {
		if (!job.uses) continue;
		calls++;
		const calleeFile = job.uses.replace('./.github/workflows/', '');
		const callee = parsed.get(calleeFile);
		if (!callee) fail(`${file}: job ${name} calls ${calleeFile}, which does not exist`);

		const { perms, from } = granted(wf, job);
		for (const [scope, need] of required(callee)) {
			if (!perms) {
				findings.push({ kind: 'unverifiable', file, job, jobName: name, calleeFile, scope, need: need.level, where: need.where, from });
				continue;
			}
			const have = perms.all ?? perms[scope] ?? 'none';
			if (RANK[have] < RANK[need.level]) {
				findings.push({ kind: 'impossible-grant', file, jobName: name, calleeFile, scope, need: need.level, have, where: need.where, from });
			}
		}
	}
}

const isAccepted = (f) => ACCEPTED.some((a) => a.caller === f.file && a.job === f.jobName && a.scope === f.scope);
const unaccepted = findings.filter((f) => !isAccepted(f));
const staleAcceptances = ACCEPTED.filter(
	(a) => !findings.some((f) => f.file === a.caller && f.jobName === a.job && f.scope === a.scope),
);

if (json) {
	console.log(JSON.stringify({ workflows: parsed.size, calls, findings, staleAcceptances }, null, 2));
} else {
	console.log(`[check-workflow-permissions] ${parsed.size} workflows, ${calls} local reusable-workflow calls`);
	console.log(`  findings: ${findings.length}  (${findings.length - unaccepted.length} accepted, ${unaccepted.length} unaccepted)\n`);
	for (const f of unaccepted) {
		if (f.kind === 'impossible-grant') {
			console.log(`  IMPOSSIBLE GRANT  ${f.file} job '${f.jobName}' -> ${f.calleeFile}`);
			console.log(`      grants ${f.scope}: ${f.have} (from ${f.from}); callee needs ${f.scope}: ${f.need} in its ${f.where}`);
			console.log(`      GitHub rejects the whole run with startup_failure. Add to the calling job:`);
			console.log(`          permissions:\n            ${f.scope}: ${f.need}\n`);
		} else {
			console.log(`  UNVERIFIABLE      ${f.file} job '${f.jobName}' -> ${f.calleeFile}`);
			console.log(`      neither the job nor the workflow declares permissions, so the token takes the`);
			console.log(`      repository default; callee needs ${f.scope}: ${f.need} in its ${f.where}\n`);
		}
	}
	for (const a of staleAcceptances) console.log(`  STALE ACCEPTANCE  ${a.caller} job '${a.job}' scope '${a.scope}' is no longer a finding — remove it`);
	if (unaccepted.length === 0 && staleAcceptances.length === 0) {
		console.log('  Every reusable-workflow call grants at least what its callee asks for.');
	}
}

process.exit(unaccepted.length > 0 || staleAcceptances.length > 0 ? 1 : 0);
