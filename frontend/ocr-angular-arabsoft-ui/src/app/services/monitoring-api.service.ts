/**
 * src/app/services/monitoring-api.service.ts
 *
 * Interroge le backend (pas Prometheus directement) pour recuperer un
 * resume des metriques operationnelles, deja agrege et pret a afficher.
 */
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { AuthApiService } from './auth-api.service';

export interface FieldOutcome {
  label: string;
  value: number;
}

export interface DurationByTemplate {
  label: string;
  value: number;
}

export interface MetricsSummary {
  confidence_avg_pct: number;
  extraction_rate_per_sec: number;
  circuit_breakers_open: number;
  active_jobs: number;
  duration_p95_by_template: DurationByTemplate[];
  field_outcomes: FieldOutcome[];
  prometheus_available: boolean;
}

@Injectable({ providedIn: 'root' })
export class MonitoringApiService {
  private readonly baseUrl = environment.apiBaseUrl.replace(/\/$/, '');

  constructor(
    private http: HttpClient,
    private auth: AuthApiService
  ) {}

  getMetricsSummary(): Observable<MetricsSummary> {
    return this.http.get<MetricsSummary>(`${this.baseUrl}/monitoring/metrics-summary`, {
      headers: this.auth.authHeaders(),
    });
  }
}