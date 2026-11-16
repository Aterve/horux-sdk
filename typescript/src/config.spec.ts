import { test } from 'node:test';
import assert from 'node:assert/strict';

import { defaultConfig, resolveConfig, validateConfig } from './config';

/**
 * Configuration resolution had no tests at all, and one of the defects it hid
 * was a helper that silently disagreed with the resolver about a pacing bound.
 */

const required = {
  ingestorUrl: 'http://localhost:3434',
  apiToken: 'tenant_x_secret',
  resourceId: 'resource-1',
};

test('the buffer defaults to one hundred times the batch', () => {
  const resolved = resolveConfig({ ...required });

  assert.equal(resolved.maxBatchSize, 1000);
  assert.equal(resolved.maxBufferSize, 100_000);
});

// defaultConfig used to pin maxBatchSize to 100 where resolveConfig defaults it
// to 1000. Because maxBufferSize derives from maxBatchSize, anyone following
// the documented helper got a 10x smaller buffer and began dropping telemetry
// 10x sooner during an outage.
test('defaultConfig resolves to the same bounds as a bare config', () => {
  const fromHelper = resolveConfig(defaultConfig(required));
  const fromBare = resolveConfig({ ...required });

  assert.equal(fromHelper.maxBatchSize, fromBare.maxBatchSize);
  assert.equal(fromHelper.maxBufferSize, fromBare.maxBufferSize);
});

test('a buffer smaller than one batch is lifted to the batch size', () => {
  const resolved = resolveConfig({ ...required, maxBatchSize: 50, maxBufferSize: 5 });

  assert.equal(resolved.maxBufferSize, 50, 'a buffer below one batch could never send anything');
});

test('maxRetryBackoff is lifted to min rather than inverted', () => {
  const resolved = resolveConfig({ ...required, minRetryBackoff: 5000, maxRetryBackoff: 1000 });

  assert.equal(resolved.maxRetryBackoff, 5000);
});

test('required fields are validated', () => {
  assert.throws(() => validateConfig({ ...required, ingestorUrl: '' }), /ingestorUrl/);
  assert.throws(() => validateConfig({ ...required, apiToken: '' }), /apiToken/);
  assert.throws(() => validateConfig({ ...required, resourceId: '' }), /resourceId/);
  assert.throws(() => validateConfig({ ...required, maxBatchSize: 0 }), /maxBatchSize/);
  assert.throws(
    () => validateConfig({ ...required, dropPolicy: 'sometimes' as never }),
    /dropPolicy/
  );
});

test('the drop policy defaults to oldest', () => {
  assert.equal(resolveConfig({ ...required }).dropPolicy, 'oldest');
  assert.equal(resolveConfig({ ...required, dropPolicy: 'newest' }).dropPolicy, 'newest');
});

// serviceName is the sender's own identity, carried beside the required resourceId.
// Unlike resourceId it is optional, so the resolver must not invent a value: an empty
// one has to stay empty all the way to the wire, where the ingestor stores NULL and the
// column keeps "the sender said nothing" distinguishable from a real name.
test('serviceName is optional and is not defaulted', () => {
  assert.equal(resolveConfig({ ...required }).serviceName, undefined);
  assert.equal(resolveConfig({ ...required, serviceName: 'orders-api' }).serviceName, 'orders-api');
});

test('serviceName does not make a config invalid by its absence', () => {
  assert.doesNotThrow(() => validateConfig({ ...required }));
  assert.doesNotThrow(() => validateConfig({ ...required, serviceName: 'orders-api' }));
});
