import { hostname as osHostname } from 'node:os';
import { DropPolicy, HoruxConfig } from './types';

/** A HoruxConfig with every optional field resolved to a concrete value. */
export interface ResolvedHoruxConfig {
  ingestorUrl: string;
  gatewayUrl: string;
  apiToken: string;
  resourceId: string;
  serviceName?: string;
  flushInterval: number;
  maxBatchSize: number;
  maxBufferSize: number;
  dropPolicy: DropPolicy;
  minRetryBackoff: number;
  maxRetryBackoff: number;
  httpTimeout: number;
  instanceMetadata: Record<string, any>;
  autoAddInstanceLabels: boolean;
  onDrop?: (stream: 'metrics' | 'logs', count: number, reason: string) => void;
}

/**
 * Fill in every unset option, so a hand-built config keeps working without
 * having to know about each pacing knob.
 */
export function resolveConfig(config: HoruxConfig): ResolvedHoruxConfig {
  validateConfig(config);

  const maxBatchSize = config.maxBatchSize && config.maxBatchSize > 0 ? config.maxBatchSize : 1000;
  const minRetryBackoff = config.minRetryBackoff && config.minRetryBackoff > 0 ? config.minRetryBackoff : 1000;
  const maxRetryBackoff = config.maxRetryBackoff && config.maxRetryBackoff > 0 ? config.maxRetryBackoff : 60000;

  let maxBufferSize = config.maxBufferSize && config.maxBufferSize > 0 ? config.maxBufferSize : maxBatchSize * 100;
  if (maxBufferSize < maxBatchSize) {
    maxBufferSize = maxBatchSize;
  }

  return {
    ingestorUrl: config.ingestorUrl || 'https://ingest.horux.io',
    gatewayUrl: config.gatewayUrl || '',
    apiToken: config.apiToken,
    resourceId: config.resourceId,
    serviceName: config.serviceName,
    flushInterval: config.flushInterval && config.flushInterval > 0 ? config.flushInterval : 10000,
    maxBatchSize,
    maxBufferSize,
    dropPolicy: config.dropPolicy || 'oldest',
    minRetryBackoff,
    maxRetryBackoff: Math.max(maxRetryBackoff, minRetryBackoff),
    httpTimeout: config.httpTimeout && config.httpTimeout > 0 ? config.httpTimeout : 30000,
    instanceMetadata: config.instanceMetadata || { hostname: osHostname() },
    autoAddInstanceLabels: config.autoAddInstanceLabels ?? true,
    onDrop: config.onDrop,
  };
}

/**
 * Validate a HoruxConfig, throwing if required fields are missing or invalid
 */
export function validateConfig(config: HoruxConfig): void {
  if (!config.ingestorUrl) {
    throw new Error('HoruxConfig: ingestorUrl is required');
  }
  if (!config.apiToken) {
    throw new Error('HoruxConfig: apiToken is required');
  }
  if (!config.resourceId) {
    throw new Error('HoruxConfig: resourceId is required');
  }
  if (config.flushInterval !== undefined && config.flushInterval <= 0) {
    throw new Error('HoruxConfig: flushInterval must be greater than 0');
  }
  if (config.maxBatchSize !== undefined && config.maxBatchSize <= 0) {
    throw new Error('HoruxConfig: maxBatchSize must be greater than 0');
  }
  if (config.dropPolicy !== undefined && config.dropPolicy !== 'oldest' && config.dropPolicy !== 'newest') {
    throw new Error("HoruxConfig: dropPolicy must be 'oldest' or 'newest'");
  }
}

/**
 * Build a HoruxConfig from environment variables with optional overrides.
 *
 * Environment variables:
 *   HORUX_INGESTOR_URL  → ingestorUrl
 *   HORUX_API_TOKEN     → apiToken
 *   HORUX_RESOURCE_ID   → resourceId
 *   HORUX_SERVICE_NAME  → serviceName
 *   HORUX_GATEWAY_URL   → gatewayUrl
 *   HORUX_FLUSH_INTERVAL → flushInterval (ms)
 *   HORUX_BATCH_SIZE    → maxBatchSize
 *
 * Kubernetes metadata (auto-detected when present):
 *   POD_NAME, NODE_NAME, NAMESPACE, CONTAINER_ID
 */
export function configFromEnv(overrides?: Partial<HoruxConfig>): HoruxConfig {
  const instanceMetadata: Record<string, string> = {
    hostname: osHostname(),
  };

  if (process.env.POD_NAME) instanceMetadata.pod = process.env.POD_NAME;
  if (process.env.NODE_NAME) instanceMetadata.node = process.env.NODE_NAME;
  if (process.env.NAMESPACE) instanceMetadata.namespace = process.env.NAMESPACE;
  if (process.env.CONTAINER_ID) instanceMetadata.containerId = process.env.CONTAINER_ID;

  const flushInterval = process.env.HORUX_FLUSH_INTERVAL
    ? parseInt(process.env.HORUX_FLUSH_INTERVAL, 10)
    : undefined;

  const maxBatchSize = process.env.HORUX_BATCH_SIZE
    ? parseInt(process.env.HORUX_BATCH_SIZE, 10)
    : undefined;

  return {
    ingestorUrl: process.env.HORUX_INGESTOR_URL || 'https://ingest.horux.io',
    apiToken: process.env.HORUX_API_TOKEN || '',
    resourceId: process.env.HORUX_RESOURCE_ID || '',
    serviceName: process.env.HORUX_SERVICE_NAME,
    gatewayUrl: process.env.HORUX_GATEWAY_URL,
    flushInterval,
    maxBatchSize,
    instanceMetadata,
    ...overrides,
  };
}

/**
 * Create a HoruxConfig with sensible defaults.
 *
 * Pacing options are deliberately left unset so resolveConfig supplies them.
 * This helper used to pin maxBatchSize to 100 where resolveConfig defaults it
 * to 1000, and because maxBufferSize derives from maxBatchSize, anyone
 * following the documented helper silently got a 10x smaller buffer (10,000
 * instead of 100,000 at today's multiplier) and began dropping telemetry 10x
 * sooner during an outage.
 */
export function defaultConfig(required: {
  ingestorUrl: string;
  apiToken: string;
  resourceId: string;
}): HoruxConfig {
  return {
    ingestorUrl: required.ingestorUrl,
    apiToken: required.apiToken,
    resourceId: required.resourceId,
    flushInterval: 10000,
    instanceMetadata: { hostname: osHostname() },
  };
}
