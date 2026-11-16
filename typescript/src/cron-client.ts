import { CheckInResponse } from './types';

export class CronClient {
  private gatewayUrl: string;
  private httpTimeout: number;

  constructor(gatewayUrl: string, httpTimeout: number = 30000) {
    this.gatewayUrl = gatewayUrl.replace(/\/+$/, '');
    this.httpTimeout = httpTimeout;
  }

  /**
   * Send a cron job check-in (heartbeat)
   */
  async checkIn(checkInKey: string, metadata?: Record<string, unknown>): Promise<CheckInResponse> {
    const url = `${this.gatewayUrl}/api/v1/cron-jobs/check-in/${checkInKey}`;

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.httpTimeout);

    try {
      const body = metadata ? JSON.stringify({ metadata }) : undefined;

      const response = await fetch(url, {
        method: 'POST',
        headers: body ? { 'Content-Type': 'application/json' } : {},
        body,
        signal: controller.signal,
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(`Gateway returned error: ${response.status} - ${JSON.stringify(error)}`);
      }

      const data = await response.json() as any;
      return {
        success: data.success,
        message: data.message,
        nextExpectedAt: data.next_expected_at,
      };
    } finally {
      clearTimeout(timeout);
    }
  }
}
